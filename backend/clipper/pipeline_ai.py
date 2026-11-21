"""Phase 2 orchestration: multimodal analysis, automatic director and edit plans.

Every clip ends with a :class:`DirectorDecision` and an :class:`EditPlan`:
* with an AI worker: multimodal (or heuristic-vision) analysis + director in the worker;
* without one (disabled, timeout, dead job, model unavailable everywhere): the director
  runs here in ``audio_only`` mode from data already in the database.
"""

from __future__ import annotations

import logging

from sqlalchemy import select
from sqlalchemy.orm import Session

from . import queue
from .ai import director as director_mod
from .ai import edit_plan as edit_plan_mod
from .ai.schemas import DirectorDecision, EditPlan, NarrativeResult, VisualAnalysis
from .db import utcnow
from .models import AudioFeature, Clip, Segment, StreamSession, Streamer, TranscriptWord
from .scoring.engine import weighted_total
from .settings_store import AISettings, load_ai_settings

log = logging.getLogger(__name__)

VISION_KEY_PREFIX = "vision:"


def vision_key(clip: Clip) -> str:
    return f"vision:{clip.id}" if not clip.analysis_runs else f"vision:{clip.id}:r{clip.analysis_runs}"


# ---------------------------------------------------------------------------
# routing
# ---------------------------------------------------------------------------


def enqueue_analysis(db: Session, clip: Clip, settings: AISettings, priority: int = 60) -> None:
    sess = db.get(StreamSession, clip.session_id)
    clip.status = "analyzing"
    clip.vision_state = "pending"
    model = settings.vision_model if settings.vision_provider == "openvino_vlm" else None
    queue.enqueue(
        db, "vision_analyze",
        {"clip_id": clip.id, "session_id": clip.session_id, "provider": settings.vision_provider,
         "model": model, "narrative_model": settings.narrative_model,
         "frame_interval": settings.vision_frame_interval,
         "segment_base_url": sess.segment_base_url if sess else ""},
        idempotency_key=vision_key(clip), priority=priority, max_attempts=2,
    )


def media_bounds(db: Session, clip: Clip) -> tuple[float, float]:
    """Session-offset range that can still be fetched for this clip."""
    lo_hi = db.execute(
        select(Segment.start, Segment.duration).where(Segment.session_id == clip.session_id,
                                                      Segment.available.is_(True))
    ).all()
    lo = min((s for s, _ in lo_hi), default=None)
    hi = max((s + d for s, d in lo_hi), default=None)
    if clip.source_path:
        s_lo, s_hi = clip.source_start, clip.source_start + clip.source_duration
        lo = s_lo if lo is None else min(lo, s_lo)
        hi = s_hi if hi is None else max(hi, s_hi)
    return (lo if lo is not None else clip.start, hi if hi is not None else clip.end)


def _acoustic(db: Session, session_id: int, a: float, b: float) -> dict:
    frames, pitch = [], []
    for f in db.scalars(select(AudioFeature).where(AudioFeature.session_id == session_id,
                                                   AudioFeature.start < b, AudioFeature.start > a - 15)):
        extra = f.extra or {}
        y = extra.get("yamnet") or {}
        cats = y.get("cats") or {}
        hop = float(y.get("hop", 0.48))
        n = max((len(v) for v in cats.values()), default=0)
        for k in range(n):
            t = f.start + k * hop + hop  # centre of the 0.96 s window
            if a <= t <= b:
                frames.append([round(t, 2), {c: v[k] for c, v in cats.items() if k < len(v)}])
        ph = float(extra.get("pitch_hop", 0.25))
        for k, hz in enumerate(extra.get("pitch_hz") or []):
            t = f.start + k * ph
            if hz and a <= t <= b:
                pitch.append([round(t, 2), hz])
    frames.sort(key=lambda x: x[0])
    pitch.sort()
    return {"frames": frames, "pitch": pitch}


def _words(db: Session, session_id: int, a: float, b: float) -> list[list]:
    rows = db.execute(select(TranscriptWord.start, TranscriptWord.end, TranscriptWord.word)
                      .where(TranscriptWord.session_id == session_id, TranscriptWord.start >= a,
                             TranscriptWord.start < b).order_by(TranscriptWord.start)).all()
    return [[r[0], r[1], r[2]] for r in rows]


def _word_speakers(db: Session, session_id: int, a: float, b: float) -> list[str]:
    """Speaker label per word, aligned with :func:`_words` (kept apart: the director reads 3-tuples)."""
    return [r[0] or "" for r in db.execute(
        select(TranscriptWord.speaker).where(TranscriptWord.session_id == session_id, TranscriptWord.start >= a,
                                             TranscriptWord.start < b).order_by(TranscriptWord.start)).all()]


def _limits(db: Session, clip: Clip, settings: AISettings) -> dict:
    lo, hi = media_bounds(db, clip)
    streamer = db.get(Streamer, clip.streamer_id)
    prefs = (streamer.preferences or {}) if streamer else {}
    min_score = float(prefs["min_score"]) if isinstance(prefs.get("min_score"), (int, float)) else settings.min_score
    max_len = settings.max_clip_seconds
    if clip.situation_id and (clip.provenance or {}).get("story", {}).get("action") in ("story", "merge"):
        max_len = max(max_len, settings.max_story_seconds)
    if ((clip.provenance or {}).get("gate") or {}).get("pending"):
        max_len = max(max_len, settings.max_story_seconds)  # a rework may need the whole situation  # a multi-minute story may need a longer cut
    return {"min_len": settings.min_clip_seconds, "max_len": max_len, "avail_start": lo,
            "avail_end": hi, "min_score": min_score}


def ai_spec(db: Session, clip: Clip, source_location: dict) -> dict:
    """Everything the AI worker needs (no DB access on the worker side)."""
    settings = load_ai_settings(db)
    base_start = clip.heuristic_start if clip.heuristic_start is not None else clip.start
    base_end = clip.heuristic_end if clip.heuristic_end is not None else clip.end
    rework = ((clip.provenance or {}).get("gate") or {}).get("pending")
    if rework:  # Quality Gate REWORK: start from the proposed edit (earlier start, outcome included...)
        base_start, base_end = float(rework["start"]), float(rework["end"])
    lo, hi = media_bounds(db, clip)
    ctxs = settings.analysis_context_seconds
    w0, w1 = max(lo, base_start - ctxs), min(hi, base_end + ctxs)
    deep = (clip.provenance or {}).get("review", {}).get("pending") == "deep"
    story = _story(db, clip)
    sit = story.get("situation") or {}
    if deep:  # re-watch: the whole situation (bounded), denser frames
        if sit.get("start") is not None:
            w0 = max(lo, min(w0, float(sit["start"]) - 3.0))
            w1 = min(hi, max(w1, float(sit.get("last") or base_end) + 5.0))
        w0 = max(w0, w1 - (settings.max_story_seconds + 40.0))
    streamer = db.get(Streamer, clip.streamer_id)
    return {
        **source_location,
        "clip_id": clip.id,
        "session_id": clip.session_id,
        "start": base_start, "end": base_end, "peak": clip.peak,
        "window": [w0, w1],
        "transcript": clip.transcript,
        "words": _words(db, clip.session_id, w0, w1),
        "word_speakers": _word_speakers(db, clip.session_id, w0, w1),
        "acoustic": _acoustic(db, clip.session_id, w0, w1),
        "candidate": {"score": clip.score, "breakdown": clip.score_breakdown or {}, "reasons": clip.reasons or [],
                      "kinds": clip.kinds or []},
        "limits": _limits(db, clip, settings),
        "settings": settings.model_dump(),
        "streamer": {"game": ((streamer.preferences or {}).get("game") if streamer else "") or "",
                     "display_name": streamer.display_name if streamer else ""},
        "title": clip.title, "title_suggestions": clip.title_suggestions or [],
        "layout": clip.layout, "caption_style": clip.caption_style, "webcam_region": clip.webcam_region,
        "analysis_runs": clip.analysis_runs,
        "story": story,
        "deep": deep,
        "max_frames": settings.deep_review_max_frames if deep else None,
        "setup_t": (float(sit["start"]) if deep and sit.get("start") is not None else None),
    }


def _story(db: Session, clip: Clip) -> dict:
    try:
        from .pipeline_story import story_context

        return story_context(db, clip)
    except Exception:
        log.exception("story context unavailable")
        return {"situation": None, "before": []}


# ---------------------------------------------------------------------------
# applying results
# ---------------------------------------------------------------------------


def apply_analysis(db: Session, clip: Clip, payload: dict, *, worker: str = "") -> None:
    """Persist analysis + decision + plan, update the clip, and either render or discard."""
    settings = load_ai_settings(db)
    decision = DirectorDecision.model_validate(payload["decision"])
    plan = EditPlan.model_validate(payload["edit_plan"])
    visual = VisualAnalysis.model_validate(payload["visual"]) if payload.get("visual") else None
    narrative = NarrativeResult.model_validate(payload["narrative"]) if payload.get("narrative") else None
    heuristic = payload.get("heuristic_vision")

    if clip.heuristic_start is None:
        clip.heuristic_start, clip.heuristic_end = clip.start, clip.end
    clip.analysis = {"visual": visual.model_dump() if visual else None,
                     "narrative": narrative.model_dump() if narrative else None,
                     "heuristic_vision": heuristic}
    clip.decision = decision.model_dump()
    clip.edit_plan = plan.model_dump()
    meta = dict(payload.get("meta") or {})
    meta.update({"worker": worker, "at": utcnow().isoformat() + "Z", "mode": decision.mode,
                 "vision_fallback": bool(visual is None or visual.fallback)})
    clip.analysis_meta = meta
    prov = dict(clip.provenance or {})
    prov["analysis"] = {k: meta.get(k) for k in ("vision_model", "vision_device", "vision_provider", "narrative_provider",
                                                  "prompt_version", "max_new_tokens", "frames", "fallback_reason",
                                                  "director_version") if k in meta}
    prov["analysis"]["run"] = clip.analysis_runs + 1
    prov["analysis"]["worker"] = worker
    clip.provenance = prov
    if heuristic:
        clip.vision = heuristic
        if heuristic.get("webcam_region") and not clip.webcam_region:
            clip.webcam_region = heuristic["webcam_region"]
    breakdown = dict(clip.score_breakdown or {})
    if visual is not None and not visual.fallback:
        breakdown["visual"] = round(visual.interest, 2)
    elif heuristic and heuristic.get("visual_score") is not None:
        breakdown["visual"] = round(float(heuristic["visual_score"]), 2)
    clip.score_breakdown = breakdown

    first = clip.analysis_runs == 0
    if clip.is_test:
        # TESTE MANUAL: keep the requested range; scoring/director can never block or move it
        clip.score = decision.score
        clip.reasons = list(dict.fromkeys(list(clip.reasons or [])[:1] + decision.reasons))[:14]
    elif settings.director_enabled:
        clip.start, clip.end, clip.peak = decision.start_abs, decision.end_abs, decision.peak_abs
        clip.score = decision.score
        clip.reasons = list(dict.fromkeys(decision.reasons + decision.adjustments))[:14]
        tr = _words(db, clip.session_id, clip.start, clip.end)
        clip.transcript = " ".join(w[2] for w in tr).strip() or clip.transcript
        _apply_playbook(db, clip, visual)
    else:
        clip.score = round(weighted_total(breakdown, settings), 1)
    if narrative is not None and not narrative.fallback and narrative.titles and not clip.is_test:
        clip.title_suggestions = list(dict.fromkeys(narrative.titles + list(clip.title_suggestions or [])))[:6]
        if first:
            clip.title = narrative.titles[0][:255]
        if first and (narrative.description or narrative.justification):
            clip.description = (narrative.description or narrative.justification) + "\n\n" + (clip.description or "")
        if narrative.hashtags:
            clip.hashtags = list(dict.fromkeys(list(narrative.hashtags) + list(clip.hashtags or [])))[:10]
    clip.analysis_runs += 1
    # done = some visual analysis ran (multimodal or heuristic); multimodal vs fallback lives in analysis_meta
    clip.vision_state = "done" if (visual is not None or heuristic) else "skipped"
    from .evaluation.dataset import refresh_clip_snapshot

    try:
        with db.begin_nested():
            refresh_clip_snapshot(db, clip)
    except Exception:
        log.exception("dataset snapshot refresh failed (clip unaffected)")

    _editorial_final(db, clip, decision, visual, settings)
    judgement = (clip.decision or {}).get("editorial") or {}
    prov = dict(clip.provenance or {})
    review = dict(prov.get("review") or {})
    if review.get("pending") == "deep":  # this IS the deep review result
        review = {"done": "deep", "first_score": review.get("first_score"), "score": decision.score,
                  "bounds_change_s": [round(decision.start_abs - (review.get("first_bounds") or [0, 0])[0], 1),
                                      round(decision.end_abs - (review.get("first_bounds") or [0, 0])[1], 1)]}
        prov["review"] = review
        clip.provenance = prov
        clip.reasons = list(dict.fromkeys(list(clip.reasons or []) + [
            f"Revisão aprofundada: nota {review['first_score']} → {decision.score:.0f}"]))[:14]
    elif visual is not None and not visual.fallback and not judgement.get("waiting_context")             and _needs_deep(db, clip, decision, judgement, settings):
        prov["review"] = {"pending": "deep", "first_score": round(decision.score, 1),
                          "first_bounds": [decision.start_abs, decision.end_abs],
                          "reason": _deep_reason(decision, judgement, settings)}
        clip.provenance = prov
        enqueue_analysis(db, clip, settings, priority=85)  # below live analysis (60)
        log.info("deep review requested", extra={"clip_id": clip.id, "reason": prov["review"]["reason"]})
        return
    if settings.gate_enabled and settings.director_enabled:
        if _apply_gate(db, clip, decision, visual, settings):
            return
        from .pipeline import enqueue_render

        clip.version += 0 if first else 1
        _structure(db, clip, settings)
        enqueue_render(db, clip)
        return
    if settings.director_enabled and not decision.keep and not clip.is_test and judgement.get("waiting_context")             and clip.analysis_runs < 3:  # bounded: never waits forever
        clip.status = "waiting"  # low preliminary score but the story is still unfolding: decide later
        clip.error = "Aguardando o desfecho da situação para decidir"
        log.info("clip waiting for story context", extra={"clip_id": clip.id})
        return
    if settings.director_enabled and not decision.keep and not clip.is_test and \
            (judgement.get("final") or 0) >= settings.min_score and (judgement.get("story") or {}).get("complete"):
        decision.keep = True  # complete story with good editorial criteria: keep despite the acoustic score
        clip.reasons = list(dict.fromkeys(list(clip.reasons or []) + [
            "Mantido pelo julgamento editorial: história completa com boas evidências"]))[:14]
    if settings.director_enabled and not decision.keep and not clip.is_test:
        clip.status = "rejected"
        clip.error = "Descartado automaticamente pelo diretor: " + "; ".join(decision.adjustments[:2] or
                                                                             ["pontuação abaixo do mínimo"])
        log.info("clip discarded by director", extra={"clip_id": clip.id, "score": decision.score})
        return
    from .pipeline import enqueue_render

    clip.version += 0 if first else 1
    enqueue_render(db, clip)


def _structure(db: Session, clip: Clip, settings: AISettings) -> None:
    """Phase 6B: alternative structure for the comparison (never blocks the main render)."""
    if settings.editor_mode == "off" or clip.is_test:
        return
    from . import pipeline_structure as ps

    try:
        with db.begin_nested():
            v = dict((clip.provenance or {}).get("variants") or {})
            v.pop("edit", None)  # a new analysis invalidates an older alternative
            prov = dict(clip.provenance or {})
            prov["variants"] = v
            clip.provenance = prov
            if settings.editor_mode == "shadow":
                ps.plan_variant(db, clip, settings)  # main = original, variant = restructured
            else:  # "on": main = restructured, variant = original for the comparison
                prop = ps.propose(db, clip, settings)
                if prop["applicable"] and prop["meaningful"]:
                    segs = prop["plan"]["segments"]
                    ep = dict(clip.edit_plan or {})
                    ep.update({"segments": segs, "structure": prop["structure"], "structure_reason": prop["reason"]})
                    clip.edit_plan = ep
                    v["original"] = {"structure": "O", "label": "Original", "metrics": prop["original_metrics"],
                                     "plan": {"segments": [{"a": clip.start, "b": clip.end, "role": "main"}]},
                                     "start": clip.start, "end": clip.end}
                    clip.start, clip.end = min(s["a"] for s in segs), max(s["b"] for s in segs)
                    prov["variants"] = v
                    clip.provenance = prov
                    ps.enqueue_variant(db, clip, "original")
    except Exception:
        log.exception("structural editor failed (main render unaffected)")


def visual_relevant(v: VisualAnalysis) -> bool:
    """Something that matters for the story happened ON SCREEN (not just "the VLM answered"):
    a surprising or funny event, or strong action the streamer reacts to."""
    return v.unexpected >= 6 or v.funny >= 6 or (v.action >= 7 and v.reaction_linked is True)


def _apply_gate(db: Session, clip: Clip, decision: DirectorDecision, visual: VisualAnalysis | None,
                settings: AISettings) -> bool:
    """Quality Gate 2.0. Returns True when the clip must NOT be rendered now (rework, waiting, rejected)."""
    from .ai import quality_gate as qg

    judgement = (clip.decision or {}).get("editorial") or {}
    prov = dict(clip.provenance or {})
    g = dict(prov.get("gate") or {})
    runs = int(g.get("rework_runs", 0))
    rows = db.execute(select(TranscriptWord.start, TranscriptWord.end, TranscriptWord.probability,
                             TranscriptWord.speaker).where(
        TranscriptWord.session_id == clip.session_id, TranscriptWord.start >= decision.start_abs,
        TranscriptWord.start < decision.end_abs)).all()
    spans = [(float(a), float(a) + float(d)) for a, d in db.execute(
        select(Segment.start, Segment.duration).where(Segment.session_id == clip.session_id,
                                                      Segment.available.is_(True))).all()]
    if clip.source_path and clip.source_duration:  # media already copied to the API also counts
        spans.append((clip.source_start, clip.source_start + clip.source_duration))
    lim = _limits(db, clip, settings)
    vis_ok = visual is not None and not visual.fallback
    res = qg.evaluate(
        judgement=judgement, start=decision.start_abs, end=decision.end_abs, peak=decision.peak_abs,
        keep=decision.keep, words=[(r[0], r[1]) for r in rows], media=spans, kinds=clip.kinds or [],
        word_probs=[r[2] for r in rows], speakers=[r[3] or "" for r in rows],
        visual_ok=vis_ok, visual_events=bool(vis_ok and visual_relevant(visual)),
        rework_runs=runs, history=g.get("history") or [],
        settings=qg.GateSettings(settings.gate_event_min, settings.gate_narrative_min, settings.gate_recovery_min,
                                 max_len=max(lim["max_len"], settings.max_story_seconds),
                                 max_rework=settings.gate_max_rework),
        is_test=clip.is_test, approved=clip.status == "approved")
    d = dict(clip.decision or {})
    d["gate"] = res.as_dict()
    clip.decision = d
    clip.gate = res.outcome
    hist = list(g.get("history") or []) + [{"run": clip.analysis_runs, "event": res.event.score,
                                            "narrative": res.narrative.score, "outcome": res.outcome,
                                            "bounds": [round(decision.start_abs, 2), round(decision.end_abs, 2)]}]
    g.update({"history": hist[-6:], "outcome": res.outcome})
    g.pop("pending", None)
    reason = res.reasons[0] if res.reasons else ""
    if res.outcome == qg.REWORK:
        start, end = decision.start_abs, decision.end_abs
        for f in res.fixes:
            if f.applicable and f.start is not None:
                start = f.start if f.kind == "context_before" else max(start, f.start)
            if f.applicable and f.end is not None:
                end = max(end, f.end)
        g.update({"rework_runs": runs + 1, "pending": {"start": round(start, 2), "end": round(end, 2),
                                                        "fixes": [f.kind for f in res.fixes if f.applicable]}})
        prov["gate"] = g
        clip.provenance = prov
        clip.reasons = list(dict.fromkeys(list(clip.reasons or []) + [f"Quality Gate: {reason}"]))[:14]
        enqueue_analysis(db, clip, settings, priority=85)  # below live analysis
        log.info("quality gate rework", extra={"clip_id": clip.id, "run": runs + 1, "bounds": [start, end]})
        return True
    prov["gate"] = g
    clip.provenance = prov
    if res.outcome == qg.CONTEXT_PENDING and clip.analysis_runs < 3:
        clip.status = "waiting"
        clip.error = "Aguardando o desfecho da situação para decidir"
        return True
    if res.outcome == qg.REJECTED:
        clip.status = "rejected"
        clip.error = "Descartado pelo Quality Gate: " + reason
        log.info("clip rejected by quality gate", extra={"clip_id": clip.id, "reason": reason})
        return True
    if res.outcome in (qg.HUMAN_REVIEW, qg.CONTEXT_PENDING):
        clip.reasons = list(dict.fromkeys(list(clip.reasons or []) + [f"Revisão humana: {reason}"]))[:14]
    return False  # READY / HUMAN_REVIEW: render so a person can watch it


def _needs_deep(db: Session, clip: Clip, decision: DirectorDecision, judgement: dict, settings: AISettings) -> bool:
    if not settings.deep_review_enabled or clip.is_test or "review" in (clip.provenance or {}):
        return False
    # The second (deep) analysis is a premium feature for customer channels; HTS channels keep it
    from . import entitlements
    from .models import Job, Streamer

    if not entitlements.has_feature(db, db.get(Streamer, clip.streamer_id), "secondPass"):
        return False

    queued = db.query(Job).filter(Job.type == "vision_analyze", Job.status == "queued").count()
    if queued >= settings.vision_queue_max:
        return False  # under load the live analysis has priority
    near = abs(decision.score - settings.min_score) <= settings.deep_review_margin
    weak_evidence = (judgement.get("confidence") or 1.0) < 0.5 and         abs(decision.score - settings.min_score) <= 2 * settings.deep_review_margin  # only in the doubtful band
    story = judgement.get("story") or {}
    cut_story = bool(story) and story.get("start") is not None and float(story["start"]) < decision.start_abs - 2
    return near or weak_evidence or cut_story


def _deep_reason(decision: DirectorDecision, judgement: dict, settings: AISettings) -> str:
    if abs(decision.score - settings.min_score) <= settings.deep_review_margin:
        return f"nota {decision.score:.0f} perto do mínimo {settings.min_score:.0f}"
    if (judgement.get("confidence") or 1.0) < 0.5:
        return "poucas evidências independentes"
    return "a situação começa antes do corte"


def _apply_playbook(db: Session, clip: Clip, visual: VisualAnalysis | None) -> None:
    """Phase 7: professional editing checks (ai/playbook.py) adjust the score and propose edit fixes.
    Never blocks the pipeline; the evidence is kept in provenance['playbook']."""
    try:
        from .ai import playbook
        from .pipeline_story import story_context

        words = _words(db, clip.session_id, clip.start - 5.0, clip.end + 3.0)
        story = story_context(db, clip)["situation"]
        vis = visual.model_dump() if visual is not None else None
        craft = playbook.assess(words=words, start=clip.start, end=clip.end, peak=clip.peak, kinds=clip.kinds,
                                story=story, visual=vis)
        fixes = playbook.edit_fixes(craft, start=clip.start, end=clip.end, peak=clip.peak, words=words)
        before = float(clip.score)
        clip.score = playbook.adjust(before, craft)
        prov = dict(clip.provenance or {})
        prov["playbook"] = {"version": playbook.VERSION, "score_before": before, "score_after": clip.score,
                            "offgame": craft.offgame, "fixes": fixes,
                            "criteria": {k: {"value": v, "evidence": ev} for k, (v, ev) in craft.criteria.items()}}
        clip.provenance = prov
        if craft.offgame:
            clip.reasons = (craft.notes + list(clip.reasons or []))[:14]
    except Exception:
        log.exception("playbook failed (clip unaffected)")


def _playbook_inputs(db: Session, clip: Clip, visual: VisualAnalysis | None):
    try:
        from .ai import playbook
        from .pipeline_story import story_context

        words = _words(db, clip.session_id, clip.start - 5.0, clip.end + 3.0)
        craft = playbook.assess(words=words, start=clip.start, end=clip.end, peak=clip.peak, kinds=clip.kinds,
                                story=story_context(db, clip)["situation"],
                                visual=visual.model_dump() if visual is not None else None)
        return craft, (clip.provenance or {}).get("playbook", {}).get("fixes") or []
    except Exception:
        log.exception("playbook inputs failed")
        return None, []


def _editorial_final(db: Session, clip: Clip, decision: DirectorDecision, visual: VisualAnalysis | None,
                     settings: AISettings) -> None:
    try:
        from .ai.editorial import judge
        from .pipeline_story import story_context

        ctx = story_context(db, clip)
        craft, fixes = _playbook_inputs(db, clip, visual)
        sib = 0
        if clip.situation_id:
            sib = db.query(Clip).filter(Clip.situation_id == clip.situation_id, Clip.id != clip.id,
                                        Clip.status.in_(("ready", "approved"))).count()
        j = judge(start=decision.start_abs, end=decision.end_abs, peak=decision.peak_abs,
                  breakdown=clip.score_breakdown or {}, kinds=clip.kinds or [], story=ctx["situation"],
                  visual=visual.model_dump() if visual is not None else None, director=decision.model_dump(),
                  same_story_clips=sib, min_score=settings.min_score, craft=craft, fixes=fixes)
        clip.editorial_score, clip.confidence = j.final, j.confidence
        d = dict(clip.decision or {})
        d["editorial"] = j.as_dict()
        clip.decision = d
    except Exception:
        log.exception("final editorial judgement failed (clip unaffected)")


def direct_without_vision(db: Session, clip: Clip, reason: str) -> None:
    """Fallback: run the director in audio_only mode inside the API (no AI worker)."""
    settings = load_ai_settings(db)
    lim = _limits(db, clip, settings)
    base_start = clip.heuristic_start if clip.heuristic_start is not None else clip.start
    base_end = clip.heuristic_end if clip.heuristic_end is not None else clip.end
    w0, w1 = max(lim["avail_start"], base_start - 10), min(lim["avail_end"], base_end + 10)
    ac = _acoustic(db, clip.session_id, w0, w1)
    words = _words(db, clip.session_id, w0, w1)
    cand = director_mod.Candidate(base_start, base_end, clip.peak, clip.score, clip.score_breakdown or {},
                                  list(clip.reasons or []), list(clip.kinds or []))
    track = director_mod.AcousticTrack([(t, c) for t, c in ac["frames"]], [(t, p) for t, p in ac["pitch"]])
    decision = director_mod.decide(cand, words, track, None, director_mod.Limits(**lim))
    decision.reasons.append(f"Análise visual indisponível ({reason[:90]})")
    clip_words = [w[2] for w in words if decision.start_abs <= w[0] < decision.end_abs]
    plan = edit_plan_mod.build(decision, template_key=settings.edit_template, title=clip.title,
                               transcript_words=clip_words, webcam_region=clip.webcam_region,
                               normalize_audio=settings.normalize_audio, zoom_intensity=settings.zoom_intensity,
                               layout_override=None if clip.layout == "auto" else clip.layout)
    plan.base_start_abs = decision.start_abs
    apply_analysis(db, clip, {"decision": decision.model_dump(), "edit_plan": plan.model_dump(),
                              "meta": {"fallback_reason": reason, "vision_model": None}}, worker="api")


def reanalyze(db: Session, clip: Clip) -> None:
    """Manual reprocessing: new analysis job (bounds restart from the heuristic candidate)."""
    settings = load_ai_settings(db)
    if not settings.vision_enabled:
        direct_without_vision(db, clip, "análise visual desativada nas configurações")
        return
    enqueue_analysis(db, clip, settings)


# ---------------------------------------------------------------------------
# rendering
# ---------------------------------------------------------------------------


def effective_plan(clip: Clip, settings: AISettings) -> dict:
    """Stored plan + user overrides, with times shifted to the current clip start (so a
    re-edit reuses the analysis without running it again)."""
    if clip.edit_plan:
        plan = EditPlan.model_validate(clip.edit_plan)
    else:
        plan = EditPlan(template=settings.edit_template, caption_style=clip.caption_style,
                        normalize_audio=settings.normalize_audio, base_start_abs=clip.start)
    shift = (plan.base_start_abs or clip.start) - clip.start
    dur = clip.end - clip.start
    plan.crop_keyframes = [k.model_copy(update={"t": round(k.t + shift, 2)}) for k in plan.crop_keyframes
                           if 0 <= k.t + shift <= dur] or plan.crop_keyframes[:1]
    plan.zooms = [z.model_copy(update={"t": round(z.t + shift, 2)}) for z in plan.zooms
                  if 0 <= z.t + shift and z.t + shift + z.duration <= dur]
    plan.face_track = [{**f, "t": round(f["t"] + shift, 2)} for f in plan.face_track if 0 <= f["t"] + shift <= dur]
    plan.base_start_abs = clip.start
    if clip.layout and clip.layout != "auto":
        plan.layout = clip.layout
        plan.source = "user"
    if plan.layout == "split" and not (clip.webcam_region or plan.webcam_region):
        plan.layout = "gameplay"
    plan.webcam_region = clip.webcam_region or plan.webcam_region
    plan.caption_style = clip.caption_style or plan.caption_style
    plan.title = clip.title if settings.show_title_overlay and plan.template != "minimalista" else ""
    return plan.model_dump()
