"""Evaluation dataset: examples (frozen snapshots + own media), splits and export.

* An example freezes what the system saw (transcript, acoustic events, visual analysis,
  temporal memory) and what it decided, with the versions that produced it.
* Media is re-encoded to 720p by the render worker so examples outlive the capture
  ring buffer and the clip itself (retention/quota apply).
* Train/validation split is by **session**: near-identical moments of the same live can
  never end up on both sides.
"""

from __future__ import annotations

import hashlib
import logging

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .. import queue
from ..db import utcnow
from ..models import Candidate, Clip, EvalExample, Event, Review, Streamer, StreamSession, TranscriptWord
from . import taxonomy
from .versions import provider_of

log = logging.getLogger(__name__)

MEDIA_MARGIN_S = 20.0  # extra footage around the moment so the reviewer can judge the context
VAL_FRACTION = 0.25


def split_for(session_id: int | None, streamer_id: int | None) -> str:
    """Deterministic split by session (hash), so it never changes and never leaks."""
    key = f"session:{session_id}" if session_id is not None else f"streamer:{streamer_id}"
    h = int(hashlib.sha1(key.encode()).hexdigest()[:8], 16)
    return "val" if (h % 1000) / 1000.0 < VAL_FRACTION else "train"


def context_snapshot(db: Session, session_id: int, a: float, b: float) -> dict:
    words = db.execute(select(TranscriptWord.start, TranscriptWord.word)
                       .where(TranscriptWord.session_id == session_id, TranscriptWord.start >= a,
                              TranscriptWord.start < b).order_by(TranscriptWord.start).limit(1500)).all()
    events = db.scalars(select(Event).where(Event.session_id == session_id, Event.t >= a, Event.t < b)
                        .order_by(Event.t).limit(300)).all()
    return {
        "transcript": " ".join(w for _, w in words),
        "words": [[round(t, 2), w] for t, w in words],
        "acoustic_events": [{"t": round(e.t, 2), "kind": e.kind, "strength": round(e.strength, 2),
                             **{k: v for k, v in (e.data or {}).items() if k in ("phrase", "category", "source", "by")}}
                            for e in events],
    }


def ai_from_candidate(c: Candidate, min_score: float | None) -> dict:
    return {"decision": c.decision, "score": c.score, "min_score": min_score, "start": c.start, "end": c.end,
            "peak": c.peak, "breakdown": c.breakdown, "kinds": c.kinds, "situation": c.situation,
            "ranker_score": c.ranker_score, "provider": "heuristic"}


def ai_from_clip(clip: Clip) -> dict:
    analysis = clip.analysis or {}
    visual = analysis.get("visual") or {}
    decision = clip.decision or {}
    return {
        "decision": "accepted", "clip_id": clip.id, "clip_status": clip.status, "score": clip.score,
        "start": clip.start, "end": clip.end, "peak": clip.peak,
        "heuristic_start": clip.heuristic_start, "heuristic_end": clip.heuristic_end,
        "breakdown": clip.score_breakdown, "kinds": clip.kinds, "reasons": clip.reasons, "title": clip.title,
        "provider": provider_of(clip),
        "visual": {k: visual.get(k) for k in ("summary", "key_event", "funny", "unexpected", "important", "action",
                                               "events", "model", "device", "interest")} if visual else None,
        "director": {k: decision.get(k) for k in ("action", "final_score", "adjustments", "dimensions", "evidence",
                                                   "explanation") if k in decision} if decision else None,
        "fallback_reason": (clip.analysis_meta or {}).get("fallback_reason", ""),
    }


def _streamer_info(db: Session, streamer_id: int | None) -> tuple[str, str]:
    s = db.get(Streamer, streamer_id) if streamer_id else None
    return (s.display_name or s.channel, s.platform) if s else ("", "")


def create_example(db: Session, *, stratum: str, priority: float, candidate: Candidate | None = None,
                   clip: Clip | None = None, min_score: float | None = None,
                   media_source: str = "segments") -> EvalExample | None:
    """Create a dataset example and queue the extraction of its media. Idempotent per candidate/clip."""
    if candidate is not None:
        found = db.scalar(select(EvalExample).where(EvalExample.candidate_id == candidate.id))
        if found is not None:
            return found
    if clip is not None:
        found = db.scalar(select(EvalExample).where(EvalExample.clip_id == clip.id))
        if found is not None:
            return found
    src = clip if clip is not None else candidate
    session_id, streamer_id = src.session_id, src.streamer_id
    peak = src.peak
    start = src.start if src.start is not None else peak - 10
    end = src.end if src.end is not None else peak + 10
    a, b = max(0.0, start - MEDIA_MARGIN_S), end + MEDIA_MARGIN_S
    name, platform = _streamer_info(db, streamer_id)
    ai = ai_from_clip(clip) if clip is not None else ai_from_candidate(candidate, min_score)
    if clip is not None and candidate is not None:
        ai["candidate_score"] = candidate.score
        ai["ranker_score"] = candidate.ranker_score
    prov = dict((clip.provenance if clip is not None else None) or {})
    if candidate is not None:
        prov.setdefault("scoring", candidate.scoring_version)
    ex = EvalExample(
        candidate_id=candidate.id if candidate is not None else None,
        clip_id=clip.id if clip is not None else None,
        session_id=session_id, streamer_id=streamer_id, streamer_name=name, platform=platform,
        stratum=stratum, priority=round(priority, 2), split=split_for(session_id, streamer_id),
        is_test=bool(clip is not None and clip.is_test), media_start=round(a, 2), media_end=round(b, 2),
        ai=ai, context=context_snapshot(db, session_id, a, b), provenance=prov, ai_runs=[],
        media_state="pending",
    )
    sess = db.get(StreamSession, session_id)
    if sess is not None:
        ex.context["session_title"] = sess.title
    db.add(ex)
    db.flush()
    queue.enqueue(db, "dataset_media", {"example_id": ex.id, "prefer": media_source},
                  idempotency_key=f"dataset_media:{ex.id}", priority=90, max_attempts=3)
    return ex


def refresh_clip_snapshot(db: Session, clip: Clip) -> None:
    """Keep the example of a clip in sync with its latest analysis (until it is reviewed)."""
    ex = db.scalar(select(EvalExample).where(EvalExample.clip_id == clip.id))
    if ex is None or ex.reviewed_at is not None:
        return
    prev = {k: ex.ai.get(k) for k in ("candidate_score", "ranker_score") if ex.ai.get(k) is not None}
    ai = ai_from_clip(clip)
    ai.update(prev)
    ex.ai = ai
    ex.provenance = dict(clip.provenance or {})
    vis = (clip.analysis or {}).get("visual") or {}
    if vis and ex.stratum == "accepted":
        # audio and vision disagree -> most useful to review (active learning)
        interest = vis.get("interest")
        if interest is not None and ((clip.score >= 70 and interest < 3.5) or (clip.score < 60 and interest >= 7)):
            ex.priority = max(ex.priority, 85.0)
            ex.context["disagreement"] = {"audio_score": clip.score, "visual_interest": interest}


def review_queue(db: Session, limit: int = 30, only_pending: bool = True) -> list[EvalExample]:
    q = select(EvalExample).where(EvalExample.media_state == "ready")
    if only_pending:
        q = q.where(EvalExample.reviewed_at.is_(None))
    return list(db.scalars(q.order_by(EvalExample.priority.desc(), EvalExample.created_at.desc()).limit(limit)))


def counts(db: Session) -> dict:
    rows = db.execute(select(EvalExample.stratum, EvalExample.split, EvalExample.media_state,
                             EvalExample.reviewed_at.is_not(None), func.count())
                      .group_by(EvalExample.stratum, EvalExample.split, EvalExample.media_state,
                                EvalExample.reviewed_at.is_not(None))).all()
    out = {"total": 0, "reviewed": 0, "by_stratum": {}, "by_split": {}, "by_media": {}}
    for stratum, split, media, reviewed, n in rows:
        out["total"] += n
        out["reviewed"] += n if reviewed else 0
        for key, val in (("by_stratum", stratum), ("by_split", split), ("by_media", media)):
            d = out[key].setdefault(val, {"total": 0, "reviewed": 0})
            d["total"] += n
            d["reviewed"] += n if reviewed else 0
    ratings = db.execute(select(Review.rating, func.count()).group_by(Review.rating)).all()
    out["by_rating"] = {r: n for r, n in ratings}
    out["media_bytes"] = int(db.scalar(select(func.coalesce(func.sum(EvalExample.media_bytes), 0))) or 0)
    return out


def current_review(db: Session, ex: EvalExample) -> Review | None:
    return db.scalar(select(Review).where(Review.example_id == ex.id).order_by(Review.updated_at.desc()))


def export_rows(db: Session, split: str | None = None, reviewed_only: bool = True):
    """One dict per example: snapshot + human label + versions (JSONL export)."""
    q = select(EvalExample).order_by(EvalExample.id)
    if split:
        q = q.where(EvalExample.split == split)
    for ex in db.scalars(q):
        rv = current_review(db, ex)
        if reviewed_only and rv is None:
            continue
        cand = db.get(Candidate, ex.candidate_id) if ex.candidate_id else None
        label = None
        if rv is not None:
            label = {
                "rating": rv.rating, "rating_value": taxonomy.RATING_VALUE.get(rv.rating),
                "positive": rv.rating in taxonomy.POSITIVE if rv.rating != "fair" else None,
                "category": rv.category, "editorial_reasons": rv.editorial_reasons,
                "technical_issues": rv.technical_issues, "description": rv.description, "notes": rv.notes,
                "context_quality": rv.context_quality, "ideal_duration": rv.ideal_duration,
                "event": [rv.event_start, rv.event_peak, rv.event_end], "short": [rv.short_start, rv.short_end],
                "reviewer": rv.reviewer, "reviewed_at": rv.updated_at.isoformat() + "Z", "ai_judged": rv.ai_snapshot,
            }
        yield {
            "example_id": ex.id, "split": ex.split, "stratum": ex.stratum, "is_test": ex.is_test,
            "usable_as_positive": not ex.is_test,
            "streamer": {"id": ex.streamer_id, "name": ex.streamer_name, "platform": ex.platform},
            "session_id": ex.session_id, "media_window": [ex.media_start, ex.media_end],
            "media": ex.media_path if ex.media_state == "ready" else None,
            "ai": ex.ai, "features": cand.features if cand is not None else None,
            "context": ex.context, "provenance": ex.provenance, "ai_runs": ex.ai_runs, "label": label,
            "created_at": ex.created_at.isoformat() + "Z",
        }


def mark_reviewed(ex: EvalExample) -> None:
    ex.reviewed_at = utcnow()
