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
        max_len = max(max_len, settings.max_story_seconds)  # a multi-minute story may need a longer cut
    return {"min_len": settings.min_clip_seconds, "max_len": max_len, "avail_start": lo,
            "avail_end": hi, "min_score": min_score}


def ai_spec(db: Session, clip: Clip, source_location: dict) -> dict:
    """Everything the AI worker needs (no DB access on the worker side)."""
    settings = load_ai_settings(db)
    base_start = clip.heuristic_start if clip.heuristic_start is not None else clip.start
    base_end = clip.heuristic_end if clip.heuristic_end is not None else clip.end
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


def _needs_deep(db: Session, clip: Clip, decision: DirectorDecision, judgement: dict, settings: AISettings) -> bool:
    if not settings.deep_review_enabled or clip.is_test or "review" in (clip.provenance or {}):
        return False
    from .models import Job

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


def _editorial_final(db: Session, clip: Clip, decision: DirectorDecision, visual: VisualAnalysis | None,
                     settings: AISettings) -> None:
    try:
        from .ai.editorial import judge
        from .pipeline_story import story_context

        ctx = story_context(db, clip)
        sib = 0
        if clip.situation_id:
            sib = db.query(Clip).filter(Clip.situation_id == clip.situation_id, Clip.id != clip.id,
                                        Clip.status.in_(("ready", "approved"))).count()
        j = judge(start=decision.start_abs, end=decision.end_abs, peak=decision.peak_abs,
                  breakdown=clip.score_breakdown or {}, kinds=clip.kinds or [], story=ctx["situation"],
                  visual=visual.model_dump() if visual is not None else None, director=decision.model_dump(),
                  same_story_clips=sib, min_score=settings.min_score)
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
