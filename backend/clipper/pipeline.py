"""Pipeline orchestration (task scheduler side).

Every function runs inside one DB transaction supplied by the caller, so results and the
follow-up jobs they trigger are committed atomically: a crash never leaves "results
stored but next stage not enqueued" (or the reverse). All handlers are idempotent.
"""

from __future__ import annotations

import logging
from datetime import timedelta

from sqlalchemy import delete, func, select, update
from sqlalchemy.orm import Session

from . import queue
from .config import get_settings
from .db import utcnow
from .models import AudioFeature, AuditLog, Candidate, Clip, Event, Job, Segment, Streamer, StreamSession, TranscriptWord, Worker
from .scoring.engine import weighted_total
from .settings_store import AISettings, load_ai_settings

log = logging.getLogger(__name__)

SOURCE_MARGIN_S = 15.0  # room for the director to extend context / resolution
TIMELINE_LOOKBACK_S = 150.0


class Conflict(Exception):
    pass


class NotFound(Exception):
    pass


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def active_session(db: Session, streamer_id: int) -> StreamSession | None:
    return db.scalar(
        select(StreamSession)
        .where(StreamSession.streamer_id == streamer_id, StreamSession.status == "live")
        .order_by(StreamSession.id.desc())
    )


def capture_job_for(db: Session, session_id: int) -> Job | None:
    return db.scalar(select(Job).where(Job.idempotency_key == f"capture:{session_id}"))


def streamer_info(s: Streamer) -> dict:
    return {
        "id": s.id,
        "platform": s.platform,
        "channel": s.channel,
        "display_name": s.display_name or s.channel,
        "game": (s.preferences or {}).get("game", "GTA V"),
    }


# ---------------------------------------------------------------------------
# monitor
# ---------------------------------------------------------------------------


def monitor_targets(db: Session, min_interval_s: float) -> list[dict]:
    now = utcnow()
    out = []
    for s in db.scalars(select(Streamer).where(Streamer.enabled.is_(True)).order_by(Streamer.id)):
        due = s.check_requested or s.last_checked_at is None or (now - s.last_checked_at).total_seconds() >= min_interval_s
        if not due:
            continue
        sess = active_session(db, s.id)
        out.append({"streamer_id": s.id, "platform": s.platform, "url": s.url,
                    "active_session_id": sess.id if sess else None})
    return out


def report_status(db: Session, streamer_id: int, live: bool, title: str = "", error: str = "",
                  note: str = "", channel_id: str = "") -> dict:
    s = db.get(Streamer, streamer_id)
    if s is None:
        raise NotFound("streamer")
    now = utcnow()
    s.last_checked_at = now
    s.check_requested = False
    s.last_error = error[:1000] or (note[:1000] if not live else "")
    if channel_id and s.platform == "youtube" and s.channel.startswith("v:") and not live:
        # single-broadcast link registered before v0.2.10: follow the owner channel from now on
        from .platforms import channel_ref

        ref = channel_ref(channel_id, s.display_name or "")
        taken = db.scalar(select(Streamer.id).where(Streamer.platform == "youtube", Streamer.channel == ref.channel))
        if taken is None:
            log.info("youtube streamer migrated to its channel",
                     extra={"streamer_id": s.id, "from": s.channel, "to": ref.channel})
            s.channel, s.url, s.check_requested = ref.channel, ref.url, True
            db.add(AuditLog(user_email="monitor", action="streamer.youtube_channel", target=f"streamer:{s.id}",
                            details={"from": s.url, "to": ref.url}, ip=""))
    if error and not live:
        s.status = "error"
    else:
        s.status = "live" if live else "offline"
    if title:
        s.stream_title = title[:500]
    result: dict = {"session_id": None, "created": False}
    if not live or not s.enabled:
        return result
    s.last_live_at = now
    sess = active_session(db, s.id)
    if sess is not None:
        job = capture_job_for(db, sess.id)
        if job is not None and job.status in ("dead", "cancelled", "done"):
            # capture gave up (or finished) while the channel is still live -> start a new session
            sess.status = "failed" if job.status == "dead" else "ended"
            sess.ended_at = now
            sess.end_reason = f"capture job {job.status}"
            sess = None
    if sess is None:
        concurrent = db.scalar(select(func.count()).select_from(StreamSession).where(StreamSession.status == "live"))
        sess = StreamSession(streamer_id=s.id, status="live", title=title[:500] or s.stream_title, started_at=now)
        db.add(sess)
        db.flush()
        queue.enqueue(
            db, "capture",
            {"session_id": sess.id, "streamer_id": s.id, "platform": s.platform, "url": s.url},
            idempotency_key=f"capture:{sess.id}", priority=10, max_attempts=6,
        )
        result["created"] = True
        log.info("session started", extra={"session_id": sess.id, "streamer_id": s.id, "live_sessions": concurrent + 1})
    result["session_id"] = sess.id
    return result


# ---------------------------------------------------------------------------
# capture
# ---------------------------------------------------------------------------


def session_state(db: Session, session_id: int) -> dict:
    sess = db.get(StreamSession, session_id)
    if sess is None:
        raise NotFound("session")
    streamer = db.get(Streamer, sess.streamer_id)
    next_idx = db.scalar(select(func.max(Segment.idx)).where(Segment.session_id == session_id))
    return {
        "session_id": sess.id,
        "status": sess.status,
        "stop": bool(sess.stop_requested or sess.status != "live" or streamer is None or not streamer.enabled),
        "covered_until": sess.covered_until,
        "next_idx": (next_idx + 1) if next_idx is not None else 0,
        "started_at": sess.started_at.isoformat() + "Z",
    }


def register_segments(db: Session, session_id: int, worker_id: str, base_url: str, segments: list[dict]) -> int:
    sess = db.get(StreamSession, session_id)
    if sess is None:
        raise NotFound("session")
    settings = load_ai_settings(db)
    if base_url:
        sess.segment_base_url = base_url
        sess.capture_worker_id = worker_id
    added = 0
    for seg in segments:
        idx = int(seg["idx"])
        row = db.scalar(select(Segment).where(Segment.session_id == session_id, Segment.idx == idx))
        if row is None:
            row = Segment(session_id=session_id, idx=idx)
            db.add(row)
            added += 1
        row.run_id = int(seg.get("run_id", 0))
        row.start = float(seg["start"])
        row.duration = float(seg["duration"])
        row.size = int(seg.get("size", 0))
        row.sha256 = str(seg.get("sha256", ""))
        row.available = True
        sess.covered_until = max(sess.covered_until, row.start + row.duration)
        queue.enqueue(
            db, "audio_analyze",
            {
                "session_id": session_id, "segment_idx": idx, "start": row.start, "duration": row.duration,
                "sha256": row.sha256, "segment_url": f"{sess.segment_base_url}/segments/{session_id}/{idx}",
                "language": settings.language, "whisper_model": settings.whisper_model,
            },
            idempotency_key=f"audio:{session_id}:{idx}", priority=50, max_attempts=3,
        )
    db.flush()
    sess.segment_count = db.scalar(select(func.count()).select_from(Segment).where(Segment.session_id == session_id))
    return added


def evict_plan(db: Session, session_id: int, idxs: list[int], force: bool = False) -> list[int]:
    """Decide which old segments a capture worker may delete. Segments still needed
    (pending analysis, or covering a clip whose source has not been stored yet) are kept
    unless ``force`` (hard disk cap)."""
    allowed = []
    pinned: list[tuple[float, float]] = []
    if not force:
        for c in db.scalars(select(Clip).where(Clip.session_id == session_id, Clip.source_path == "",
                                               Clip.status.in_(("candidate", "analyzing", "rendering")))):
            pinned.append((c.start - SOURCE_MARGIN_S - 1, c.end + SOURCE_MARGIN_S + 1))
    for seg in db.scalars(select(Segment).where(Segment.session_id == session_id, Segment.idx.in_(idxs))):
        if not force:
            if seg.analysis_state == "pending":
                continue
            if any(seg.start < b and seg.start + seg.duration > a for a, b in pinned):
                continue
        seg.available = False
        if seg.analysis_state == "pending":  # forced: analysis of this segment is lost
            seg.analysis_state = "skipped"
            job = db.scalar(select(Job).where(Job.idempotency_key == f"audio:{session_id}:{seg.idx}"))
            if job is not None:
                queue.request_cancel(db, job)
        allowed.append(seg.idx)
    if force and allowed:
        sess = db.get(StreamSession, session_id)
        if sess is not None:
            db.flush()
            _advance_analyzed(db, sess)
    return sorted(allowed)


def end_session(db: Session, session_id: int, reason: str) -> None:
    sess = db.get(StreamSession, session_id)
    if sess is None:
        raise NotFound("session")
    if sess.status == "live":
        sess.status = "ended"
        sess.ended_at = utcnow()
        sess.end_reason = reason[:255]
    streamer = db.get(Streamer, sess.streamer_id)
    if streamer is not None:
        if streamer.platform == "file":
            streamer.enabled = False  # controlled test videos are one-shot
        streamer.status = "offline" if streamer.platform != "file" else "unknown"
    _maybe_enqueue_score(db, sess)
    log.info("session ended", extra={"session_id": session_id, "reason": reason})


# ---------------------------------------------------------------------------
# audio analysis
# ---------------------------------------------------------------------------


def post_analysis(db: Session, session_id: int, idx: int, data: dict) -> None:
    sess = db.get(StreamSession, session_id)
    if sess is None:
        raise NotFound("session")
    seg = db.scalar(select(Segment).where(Segment.session_id == session_id, Segment.idx == idx))
    if seg is None:
        raise NotFound("segment")
    feats = data.get("features") or {}
    db.execute(delete(AudioFeature).where(AudioFeature.session_id == session_id, AudioFeature.segment_idx == idx))
    db.execute(delete(TranscriptWord).where(TranscriptWord.session_id == session_id, TranscriptWord.segment_idx == idx))
    if data.get("skipped"):
        seg.analysis_state = "skipped"
    else:
        db.add(AudioFeature(
            session_id=session_id, segment_idx=idx, start=seg.start, hop=float(feats.get("hop", 0.25)),
            loudness_db=feats.get("loudness_db", []), centroid_hz=feats.get("centroid_hz", []),
            flux=feats.get("flux", []), extra=data.get("extra") or {},
            speech_ratio=float(data.get("speech_ratio", 0.0)),
            language=str(data.get("language", ""))[:8],
        ))
        for w in data.get("words", []):
            db.add(TranscriptWord(session_id=session_id, segment_idx=idx, start=seg.start + float(w[0]),
                                  end=seg.start + float(w[1]), word=str(w[2])[:120],
                                  probability=float(w[3]) if len(w) > 3 else 1.0))
        seg.analysis_state = "done"
    db.flush()
    if not data.get("skipped") and data.get("speech_turns") is not None and load_ai_settings(db).speakers_enabled:
        _speakers_safely(db, sess, idx, seg.start, data.get("speech_turns") or [])
    _advance_analyzed(db, sess)
    _maybe_enqueue_score(db, sess)


def _speakers_safely(db: Session, sess: StreamSession, idx: int, seg_start: float, turns: list) -> None:
    """Speaker labels are an enrichment: a failure there must never lose the segment's analysis."""
    from .pipeline_speakers import store_turns

    try:
        with db.begin_nested():
            store_turns(db, sess, idx, seg_start, turns)
    except Exception as exc:  # pragma: no cover - defensive
        log.warning("speaker labelling failed", extra={"session_id": sess.id, "segment_idx": idx,
                                                       "err": str(exc)[:200]})


def _advance_analyzed(db: Session, sess: StreamSession) -> None:
    rows = db.execute(
        select(Segment.idx, Segment.start, Segment.duration, Segment.analysis_state)
        .where(Segment.session_id == sess.id, Segment.start + Segment.duration > sess.analyzed_until - 0.01)
        .order_by(Segment.idx)
    ).all()
    until = sess.analyzed_until
    for _idx, start, dur, state in rows:
        if state == "pending":
            break
        until = max(until, start + dur)
    sess.analyzed_until = until


def _maybe_enqueue_score(db: Session, sess: StreamSession) -> None:
    settings = load_ai_settings(db)
    final = sess.status != "live" and sess.analyzed_until >= sess.covered_until - 0.05
    ready = sess.analyzed_until - settings.post_context_seconds > sess.scored_until + 5.0
    if not (ready or (final and sess.analyzed_until > sess.scored_until)):
        return
    key = f"score:{sess.id}:{'final' if final else round(sess.analyzed_until, 1)}"
    queue.enqueue(db, "score", {"session_id": sess.id}, idempotency_key=key, priority=40, max_attempts=3)


# ---------------------------------------------------------------------------
# scoring
# ---------------------------------------------------------------------------


def timeline(db: Session, session_id: int) -> dict:
    sess = db.get(StreamSession, session_id)
    if sess is None:
        raise NotFound("session")
    streamer = db.get(Streamer, sess.streamer_id)
    settings = load_ai_settings(db)
    t_to = sess.analyzed_until
    t_from = max(0.0, sess.scored_until - TIMELINE_LOOKBACK_S)
    feats = db.scalars(
        select(AudioFeature).where(AudioFeature.session_id == session_id,
                                   AudioFeature.start < t_to)
        .order_by(AudioFeature.segment_idx)
    ).all()
    feats = [f for f in feats if f.start + f.hop * len(f.loudness_db or []) >= t_from]
    words = db.execute(
        select(TranscriptWord.start, TranscriptWord.end, TranscriptWord.word, TranscriptWord.probability)
        .where(TranscriptWord.session_id == session_id, TranscriptWord.start >= t_from, TranscriptWord.start < t_to)
        .order_by(TranscriptWord.start)
    ).all()
    data_start = db.scalar(
        select(func.min(Segment.start)).where(Segment.session_id == session_id, Segment.available.is_(True))
    )
    clips = db.scalars(select(Clip).where(Clip.session_id == session_id).order_by(Clip.start)).all()
    final = sess.status != "live" and sess.analyzed_until >= sess.covered_until - 0.05
    eff = settings.model_dump()
    prefs = (streamer.preferences or {}) if streamer else {}
    if isinstance(prefs.get("min_score"), (int, float)):
        eff["min_score"] = float(prefs["min_score"])  # per-channel override
    if prefs.get("language"):
        eff["language"] = str(prefs["language"])
    return {
        "session_id": session_id,
        "from": t_from,
        "to": t_to,
        "data_start": max(t_from, data_start or 0.0),
        "scored_until": sess.scored_until,
        "final": final,
        "settings": eff,
        "streamer": streamer_info(streamer) if streamer else {},
        "features": [{"start": f.start, "hop": f.hop, "loudness_db": f.loudness_db, "centroid_hz": f.centroid_hz,
                      "flux": f.flux, "speech_ratio": f.speech_ratio, "extra": f.extra or {}} for f in feats],
        "words": [[w[0], w[1], w[2], w[3]] for w in words],
        "clips": [{"id": c.id, "start": c.start, "end": c.end, "peak": c.peak, "score": c.score,
                   "transcript": c.transcript, "kinds": c.kinds or []} for c in clips],
    }


def post_candidates(db: Session, session_id: int, from_scored_until: float, result: dict) -> list[int]:
    sess = db.get(StreamSession, session_id)
    if sess is None:
        raise NotFound("session")
    new_until = float(result.get("scored_until", sess.scored_until))
    if new_until < from_scored_until:
        raise Conflict("scored_until must be monotonic")
    # Atomic compare-and-set on the watermark: two concurrent scorers that read the same
    # value can no longer both insert the same clip (race found in a real multi-job run).
    swapped = db.execute(
        update(StreamSession)
        .where(StreamSession.id == session_id,
               StreamSession.scored_until >= from_scored_until - 1e-6,
               StreamSession.scored_until <= from_scored_until + 1e-6)
        .values(scored_until=max(new_until, from_scored_until))
    ).rowcount
    if swapped != 1:
        raise Conflict("session was scored concurrently")
    db.refresh(sess)
    _record_scoring_stats(sess, result)
    settings = load_ai_settings(db)
    for e in result.get("events", []):
        db.add(Event(session_id=session_id, t=float(e["t"]), duration=float(e.get("duration", 0)),
                     kind=str(e["kind"])[:32], strength=float(e.get("strength", 0)), data=e.get("data") or {}))
    streamer = db.get(Streamer, sess.streamer_id)
    prefs = (streamer.preferences or {}) if streamer else {}
    from .evaluation.versions import scoring_provenance

    scoring_version = str(result.get("scoring_version", ""))[:32]
    prov = scoring_provenance(settings, scoring_version)
    created = []
    new_cands: list[Candidate] = []
    tracker = None
    if settings.memory_enabled:
        try:
            from .pipeline_story import update_story

            with db.begin_nested():
                tracker = update_story(db, sess, new_until)
        except Exception:
            log.exception("story memory update failed (scoring unaffected)")
            tracker = None
    if tracker is not None:
        _revisit_waiting(db, session_id, settings)
    for r in result.get("rejected", []):
        d = r.get("detail") or {}
        row = Candidate(session_id=session_id, streamer_id=sess.streamer_id, peak=float(r["peak"]),
                        start=d.get("start"), end=d.get("end"),
                        score=d.get("score") if d.get("score") is not None else r.get("score"),
                        decision=str(r.get("reason", "other"))[:24], kinds=list(r.get("kinds") or []),
                        breakdown=d.get("breakdown") or {}, features=d.get("features") or {},
                        transcript=str(d.get("transcript", ""))[:1500], scoring_version=scoring_version)
        sit = tracker.at(row.peak) if tracker is not None else None
        row.situation = sit.kind if sit is not None else ""
        db.add(row)
        new_cands.append(row)
    ranker = None
    if settings.ranker_mode != "off":
        from .evaluation.ranker import predict, scoring_model

        ranker = scoring_model(db)

    def rank(row: Candidate) -> None:
        if ranker is not None and row.features:
            try:
                row.ranker_score = predict(ranker.params, ranker.feature_names, row.features)
            except Exception:  # a broken model must never block scoring
                log.exception("ranker prediction failed")

    for row in new_cands:
        rank(row)
    for c in result.get("candidates", []):
        sit = tracker.at(float(c["peak"])) if tracker is not None else None
        story_action, c_start, c_end, other = "isolated", float(c["start"]), float(c["end"]), None
        if sit is not None:
            from .pipeline_story import plan_cut

            story_action, c_start, c_end, other = plan_cut(db, session_id, sit, c_start, c_end, float(c["peak"]),
                                                           settings.max_story_seconds)
        if story_action in ("merge", "duplicate"):
            if story_action == "merge" and other is not None:
                other.start, other.end = c_start, c_end
                other.reasons = list(dict.fromkeys(list(other.reasons or []) + [
                    f"Sequência unida: novo momento da mesma situação em {float(c['peak']):.0f}s"]))[:14]
            db.add(Candidate(session_id=session_id, streamer_id=sess.streamer_id, peak=float(c["peak"]),
                             start=float(c["start"]), end=float(c["end"]), score=float(c["score"]),
                             decision="story_merged" if story_action == "merge" else "story_duplicate",
                             kinds=list(c.get("kinds") or []), breakdown=c.get("breakdown") or {},
                             features=c.get("features") or {}, transcript=str(c.get("transcript", ""))[:1500],
                             clip_id=other.id if other is not None else None, scoring_version=scoring_version,
                             situation=sit.kind if sit else ""))
            continue
        c = {**c, "start": c_start, "end": c_end}
        clip = Clip(
            session_id=session_id, streamer_id=sess.streamer_id, start=float(c["start"]), end=float(c["end"]),
            peak=float(c["peak"]), score=float(c["score"]), score_breakdown=c.get("breakdown") or {},
            reasons=list(c.get("reasons") or []), kinds=list(c.get("kinds") or []),
            transcript=str(c.get("transcript", "")), title=str(c.get("title", ""))[:255],
            description=str(c.get("description", "")), title_suggestions=list(c.get("title_suggestions") or []),
            hashtags=list(c.get("hashtags") or []),
            caption_style=prefs.get("caption_style") or settings.caption_style,
            layout=prefs.get("layout") or settings.default_layout,
            webcam_region=prefs.get("webcam_region"),
            version=1, provenance={**prov, "story": {"action": story_action, "situation": sit.kind if sit else None,
                                                     "related_clip": other.id if other is not None else None}},
            situation_id=sit.id if sit is not None else None,
        )
        db.add(clip)
        db.flush()
        try:
            from .ai.editorial import judge
            from .pipeline_story import story_context

            j = judge(start=clip.start, end=clip.end, peak=clip.peak, breakdown=clip.score_breakdown,
                      kinds=clip.kinds, story=story_context(db, clip)["situation"],
                      noise=tracker.noise_near(clip.peak) if tracker is not None else [],
                      same_story_clips=1 if story_action == "second_cut" else 0, min_score=settings.min_score)
            clip.prelim_score, clip.confidence = j.preliminary, j.confidence
        except Exception:
            log.exception("preliminary editorial judgement failed")
        row = Candidate(session_id=session_id, streamer_id=sess.streamer_id, peak=clip.peak, start=clip.start,
                        end=clip.end, score=clip.score, decision="accepted", kinds=clip.kinds,
                        breakdown=clip.score_breakdown, features=c.get("features") or {},
                        transcript=clip.transcript[:1500], clip_id=clip.id, scoring_version=scoring_version)
        rank(row)
        db.add(row)
        db.flush()
        if settings.dataset_enabled:
            from .evaluation.dataset import create_example

            _dataset_safely(db, lambda: create_example(
                db, stratum="accepted", candidate=row, clip=clip, min_score=settings.min_score,
                priority=60.0 if clip.score < settings.min_score + 10 else 40.0))
        _route_new_clip(db, clip, settings)
        created.append(clip.id)
    db.flush()
    if settings.dataset_enabled and new_cands:
        from .evaluation.dataset import create_example
        from .evaluation.sampler import select_for_review

        def pick():
            for cand, stratum, priority in select_for_review(db, new_cands, settings.min_score,
                                                             settings.dataset_max_per_hour):
                create_example(db, stratum=stratum, candidate=cand, priority=priority, min_score=settings.min_score)

        _dataset_safely(db, pick)
    return created


def _revisit_waiting(db: Session, session_id: int, settings: AISettings) -> None:
    """Clips held because their story was unfolding get a new analysis when it ends."""
    from datetime import timedelta

    from . import pipeline_ai
    from .models import SituationRow

    for clip in db.scalars(select(Clip).where(Clip.session_id == session_id, Clip.status == "waiting")):
        sit = db.get(SituationRow, clip.situation_id) if clip.situation_id else None
        stale = clip.updated_at is not None and utcnow() - clip.updated_at > timedelta(minutes=15)
        if sit is None or sit.status != "ongoing" or stale:
            clip.error = ""
            clip.reasons = list(dict.fromkeys(list(clip.reasons or []) + [
                "Reanalisado após o fim da situação (contexto completo)"]))[:14]
            pipeline_ai.enqueue_analysis(db, clip, settings)


def revisit_waiting_all(db: Session) -> int:
    """Maintenance: waiting clips of sessions that already ended (no more scoring runs) or held too long."""
    from datetime import timedelta

    settings = load_ai_settings(db)
    n = 0
    for clip in db.scalars(select(Clip).where(Clip.status == "waiting")):
        sess = db.get(StreamSession, clip.session_id)
        if (sess is None or sess.status != "live") or utcnow() - clip.updated_at > timedelta(minutes=20):
            from . import pipeline_ai

            clip.error = ""
            pipeline_ai.enqueue_analysis(db, clip, settings)
            n += 1
    return n


def _dataset_safely(db: Session, fn) -> None:
    """Evaluation bookkeeping is secondary: a failure there must never block clips."""
    try:
        with db.begin_nested():
            fn()
    except Exception:
        log.exception("dataset bookkeeping failed (clips unaffected)")


def _record_scoring_stats(sess: StreamSession, result: dict) -> None:
    """Keep per-session diagnostics: how many moments were rejected and why."""
    st = dict(sess.stats or {})
    rejected = [{k: v for k, v in r.items() if k != "detail"} for r in (result.get("rejected") or [])]
    counts = dict(st.get("rejected_counts") or {})
    for r in rejected:
        counts[r.get("reason", "other")] = counts.get(r.get("reason", "other"), 0) + 1
    scores = [r["score"] for r in rejected if r.get("score") is not None]
    if st.get("best_rejected_score") is not None:
        scores.append(st["best_rejected_score"])
    st.update({
        "rejected_counts": counts,
        "best_rejected_score": max(scores) if scores else None,
        "recent_rejected": (list(st.get("recent_rejected") or []) + rejected)[-15:],
        "candidates_total": int(st.get("candidates_total", 0)) + len(result.get("candidates") or []),
        "events_total": int(st.get("events_total", 0)) + len(result.get("events") or []),
        "min_score": result.get("min_score"),
        "last_scored_at": utcnow().isoformat() + "Z",
    })
    sess.stats = st


def _route_new_clip(db: Session, clip: Clip, settings: AISettings) -> None:
    from . import pipeline_ai

    if settings.vision_enabled:
        pipeline_ai.enqueue_analysis(db, clip, settings)
    else:
        pipeline_ai.direct_without_vision(db, clip, "análise visual desativada nas configurações")


def enqueue_render(db: Session, clip: Clip) -> Job:
    import uuid

    clip.status = "rendering"
    clip.error = ""
    job = queue.enqueue(db, "render", {"clip_id": clip.id, "version": clip.version, "render_id": uuid.uuid4().hex[:12]},
                        idempotency_key=f"render:{clip.id}:v{clip.version}", priority=70, max_attempts=3)
    # the delivery must carry this id (an existing job of the same key keeps its own id)
    prov = dict(clip.provenance or {})
    prov["render"] = {"expected": (job.payload or {}).get("render_id"), "version": clip.version}
    clip.provenance = prov
    return job


# ---------------------------------------------------------------------------
# vision
# ---------------------------------------------------------------------------


def vision_spec(db: Session, clip_id: int) -> dict:
    clip = db.get(Clip, clip_id)
    if clip is None:
        raise NotFound("clip")
    from . import pipeline_ai

    return pipeline_ai.ai_spec(db, clip, _source_location(db, clip))


def post_vision(db: Session, clip_id: int, result: dict | None, error: str = "") -> None:
    clip = db.get(Clip, clip_id)
    if clip is None:
        raise NotFound("clip")
    if clip.vision_state not in ("pending", "none"):
        return  # already handled (e.g. timed out and rendered)
    settings = load_ai_settings(db)
    if result:
        clip.vision = result
        clip.vision_state = "done"
        breakdown = dict(clip.score_breakdown or {})
        breakdown["visual"] = round(float(result.get("visual_score", 5.0)), 2)
        clip.score_breakdown = breakdown
        clip.score = round(weighted_total(breakdown, settings), 1)
        clip.reasons = list(clip.reasons or []) + [f"Visual: {r}" for r in result.get("reasons", [])[:3]]
        if result.get("webcam_region") and not clip.webcam_region:
            clip.webcam_region = result["webcam_region"]
        enqueue_render(db, clip)
    else:
        from . import pipeline_ai

        pipeline_ai.direct_without_vision(db, clip, error or "worker visual indisponível")


def vision_timeouts(db: Session) -> int:
    settings = load_ai_settings(db)
    cutoff = utcnow() - timedelta(seconds=settings.vision_timeout_seconds)
    n = 0
    from .pipeline_ai import vision_key

    for clip in db.scalars(select(Clip).where(Clip.vision_state == "pending", Clip.updated_at < cutoff)):
        job = db.scalar(select(Job).where(Job.idempotency_key == vision_key(clip)))
        if job is not None and job.status == "running":
            continue  # a worker is on it; its lease protects against hangs
        if job is not None:
            queue.request_cancel(db, job)
        post_vision(db, clip.id, None, "timeout - nenhum worker visual disponível")
        n += 1
    # vision jobs that died -> continue without vision
    for clip in db.scalars(select(Clip).where(Clip.vision_state == "pending")):
        job = db.scalar(select(Job).where(Job.idempotency_key == vision_key(clip)))
        if job is not None and job.status == "dead":
            post_vision(db, clip.id, None, job.last_error[:80])
            n += 1
    return n


# ---------------------------------------------------------------------------
# render
# ---------------------------------------------------------------------------


def _source_location(db: Session, clip: Clip, start: float | None = None, end: float | None = None) -> dict:
    """Where the worker should fetch source media for this clip (or for [start, end])."""
    s0 = clip.start if start is None else start
    s1 = clip.end if end is None else end
    if clip.source_path and clip.source_start <= s0 + 0.01 and \
            s1 <= clip.source_start + clip.source_duration + 0.01:
        return {"source": "api", "source_start": clip.source_start}
    sess = db.get(StreamSession, clip.session_id)
    a, b = s0 - SOURCE_MARGIN_S, s1 + SOURCE_MARGIN_S
    segs = db.scalars(select(Segment).where(Segment.session_id == clip.session_id, Segment.available.is_(True),
                                            Segment.start < b, Segment.start + Segment.duration > a)
                      .order_by(Segment.idx)).all()
    if segs and sess and sess.segment_base_url:
        return {
            "source": "segments",
            "session_id": clip.session_id,
            "segment_base_url": sess.segment_base_url,
            "range": [max(0.0, a), b],
            "segments": [{"idx": s.idx, "start": s.start, "duration": s.duration, "sha256": s.sha256} for s in segs],
        }
    if clip.source_path:  # segments gone: fall back to what we stored (clamped)
        return {"source": "api", "source_start": clip.source_start, "clamped": True}
    return {"source": "missing"}


def render_spec(db: Session, clip_id: int, variant: str = "") -> dict:
    from .pipeline_ai import effective_plan

    clip = db.get(Clip, clip_id)
    if clip is None:
        raise NotFound("clip")
    settings = load_ai_settings(db)
    loc = _source_location(db, clip)
    start, end = clip.start, clip.end
    if loc.get("clamped"):
        start = max(start, clip.source_start)
        end = min(end, clip.source_start + clip.source_duration)
    words = [[w[0], w[1], w[2], w[3]] for w in caption_words(db, clip, start - 0.5, end)]
    spec = {
        **loc,
        "clip_id": clip.id,
        "version": clip.version,
        "start": start,
        "end": end,
        "peak": clip.peak,
        "title": clip.title,
        "caption_style": clip.caption_style,
        "layout": clip.layout,
        "webcam_region": clip.webcam_region,
        "words": words,
        "captions": caption_options(db, clip, settings),
        "show_title": settings.show_title_overlay,
        "edit_plan": effective_plan(clip, settings),
        "preset": settings.export_preset,
        "crf": settings.export_crf,
        "fps": settings.export_fps,
    }
    if variant:
        from .pipeline_structure import variant_spec

        try:
            return variant_spec(db, clip, variant, spec)
        except LookupError as exc:
            raise NotFound("variant") from exc
    return spec


def caption_words(db: Session, clip: Clip, a: float, b: float) -> list[tuple[float, float, str, str]]:
    """Words for the clip's captions: the owner's corrected captions when present, else the transcript."""
    override = ((clip.provenance or {}).get("captions") or {}).get("words")
    if override:
        return [(float(w[0]), float(w[1]), str(w[2]), str(w[3]) if len(w) > 3 else "") for w in override
                if a <= float(w[0]) < b]
    rows = db.execute(
        select(TranscriptWord.start, TranscriptWord.end, TranscriptWord.word, TranscriptWord.speaker)
        .where(TranscriptWord.session_id == clip.session_id, TranscriptWord.start >= a, TranscriptWord.start < b)
        .order_by(TranscriptWord.start)
    ).all()
    return [(r[0], r[1], r[2], r[3] or "") for r in rows]


def caption_options(db: Session, clip: Clip, settings) -> dict:
    from .pipeline_speakers import profile_for

    prof = profile_for(db, clip.streamer_id)
    return {
        "mode": settings.caption_mode, "font": settings.caption_font, "size_pct": settings.caption_size_pct,
        "position": settings.caption_position, "animation": settings.caption_animation,
        "active_color": settings.caption_active_color, "emphasis_color": settings.caption_emphasis_color,
        "other_color": settings.caption_other_color,
        "streamer_confirmed": bool(prof is not None and prof.confirmed),
        "watermark": settings.brand_name if settings.brand_watermark else "",
        "watermark_position": settings.brand_watermark_position,
        "watermark_opacity": settings.brand_watermark_opacity,
        "cta": settings.brand_cta, "cta_seconds": settings.brand_cta_seconds,
    }


def store_source(db: Session, clip_id: int, rel_path: str, source_start: float, duration: float) -> None:
    clip = db.get(Clip, clip_id)
    if clip is None:
        raise NotFound("clip")
    clip.source_path = rel_path
    clip.source_start = source_start
    clip.source_duration = duration


def store_render(db: Session, clip_id: int, version: int, video_rel: str, thumb_rel: str, size: int, sha: str,
                 seconds: float, layout: str) -> bool:
    clip = db.get(Clip, clip_id)
    if clip is None:
        raise NotFound("clip")
    if version < clip.version:
        return False  # stale render of an older edit
    clip.video_path = video_rel
    clip.thumb_path = thumb_rel
    clip.file_size = size
    clip.sha256 = sha
    clip.render_seconds = seconds
    clip.status = "ready"
    clip.error = ""
    return True


def on_job_dead(db: Session, job: Job) -> None:
    """Keep pipeline state consistent when a job lands in the dead-letter queue."""
    p = job.payload or {}
    if job.type == "render" and p.get("variant"):  # a failed alternative never fails the clip
        from .pipeline_structure import variant_failed

        variant_failed(db, int(p.get("clip_id", 0)), str(p["variant"]), job.last_error or "render failed",
                       str(p.get("render_id") or ""))
    elif job.type == "render":
        mark_render_failed(db, int(p.get("clip_id", 0)), job.last_error or "render failed",
                           version=p.get("version"), render_id=str(p.get("render_id") or ""))
    elif job.type == "audio_analyze":
        seg = db.scalar(select(Segment).where(Segment.session_id == p.get("session_id"),
                                              Segment.idx == p.get("segment_idx")))
        if seg is not None and seg.analysis_state == "pending":
            seg.analysis_state = "skipped"
            sess = db.get(StreamSession, seg.session_id)
            if sess is not None:
                db.flush()
                _advance_analyzed(db, sess)
                _maybe_enqueue_score(db, sess)
    elif job.type == "capture":
        sess = db.get(StreamSession, int(p.get("session_id", 0)))
        if sess is not None and sess.status == "live":
            sess.status = "failed"
            sess.ended_at = utcnow()
            sess.end_reason = ("capture failed: " + (job.last_error or ""))[:255]
            _maybe_enqueue_score(db, sess)
    elif job.type == "vision_analyze":
        clip = db.get(Clip, int(p.get("clip_id", 0)))
        if clip is not None and clip.vision_state == "pending":
            post_vision(db, clip.id, None, job.last_error[:80])


def mark_render_failed(db: Session, clip_id: int, error: str, *, version=None, render_id: str = "") -> bool:
    """Fail the clip only for the render it is waiting for: a dead job of an older version or of
    another render id (late failure after a newer render was queued) changes nothing."""
    clip = db.get(Clip, clip_id)
    if clip is None or clip.status != "rendering":
        return False
    if version is not None and int(version) != clip.version:
        return False
    expected = ((clip.provenance or {}).get("render") or {}).get("expected")
    if render_id and expected and render_id != expected:
        return False
    clip.status = "failed"
    clip.error = error[:2000]
    return True


# ---------------------------------------------------------------------------
# workers
# ---------------------------------------------------------------------------


def worker_has_gpu(db: Session, worker_id: str) -> bool:
    w = db.get(Worker, worker_id)
    return bool(w and (w.capabilities or {}).get("gpu"))


def global_limits(db: Session) -> dict[str, int]:
    s = load_ai_settings(db)
    return {"render": s.max_parallel_renders, "audio_analyze": s.max_parallel_audio}


def mark_stale_workers(db: Session, after_s: float = 45.0) -> int:
    cutoff = utcnow() - timedelta(seconds=after_s)
    n = 0
    for w in db.scalars(select(Worker).where(Worker.status == "online", Worker.last_heartbeat_at < cutoff)):
        w.status = "offline"
        n += 1
    return n


def lease_seconds() -> int:
    return get_settings().lease_seconds
