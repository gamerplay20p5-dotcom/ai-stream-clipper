"""Quality metrics computed ONLY from human reviews (plus operational timings).

Definitions (examples of manual test clips are excluded from editorial metrics):
* positive = rated excellent/good; negative = bad/should_not_exist; fair = neither.
* precision        = positives among reviewed *accepted* examples (clips the system made).
* coverage/recall  = accepted positives / all known positives (accepted + rejected-but-positive).
* known false negatives = reviewed rejected candidates rated positive.
* false positives per hour = reviewed accepted rated negative / hours of captured stream.
* boundary error   = |AI start - human short start| and |AI end - human short end| (seconds).
* Precision@K      = per session, fraction of positives among the K best-ranked reviewed examples.
Returns ``None`` for every metric without enough data - never an invented value.
"""

from __future__ import annotations

import statistics
from collections import defaultdict

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..models import Clip, EvalExample, Job, Review, StreamSession
from . import taxonomy


def _pct(vals: list[float], q: float) -> float | None:
    if not vals:
        return None
    vals = sorted(vals)
    k = min(len(vals) - 1, max(0, int(round(q * (len(vals) - 1)))))
    return round(vals[k], 2)


def reviewed_pairs(db: Session, split: str | None = None) -> list[tuple[EvalExample, Review]]:
    q = select(EvalExample, Review).join(Review, Review.example_id == EvalExample.id).where(
        EvalExample.is_test.is_(False))
    if split:
        q = q.where(EvalExample.split == split)
    latest: dict[int, tuple] = {}
    for ex, rv in db.execute(q.order_by(Review.updated_at)):
        latest[ex.id] = (ex, rv)  # last review wins (one reviewer today)
    return list(latest.values())


def rank_score(ex: EvalExample, ranker: str) -> float | None:
    ai = ex.ai or {}
    if ranker == "heuristic":
        return ai.get("candidate_score", ai.get("score"))
    if ranker == "contextual":
        return ai.get("score")  # final score after the director (multimodal + memory)
    if ranker == "ranker":
        return ai.get("ranker_score")
    raise ValueError(ranker)


def precision_at_k(pairs, ranker: str, k: int = 5) -> float | None:
    by_session: dict[int, list] = defaultdict(list)
    for ex, rv in pairs:
        if rv.rating == "fair":
            continue
        s = rank_score(ex, ranker)
        if s is not None:
            by_session[ex.session_id or 0].append((s, rv.rating in taxonomy.POSITIVE))
    vals = []
    for items in by_session.values():
        if len(items) < 2:
            continue
        top = sorted(items, key=lambda x: -x[0])[:k]
        vals.append(sum(1 for _, p in top if p) / len(top))
    return round(statistics.mean(vals), 3) if vals else None


def editorial(db: Session, split: str | None = None) -> dict:
    pairs = reviewed_pairs(db, split)
    acc = [(ex, rv) for ex, rv in pairs if (ex.ai or {}).get("decision") == "accepted"]
    rej = [(ex, rv) for ex, rv in pairs if (ex.ai or {}).get("decision") != "accepted"]
    acc_bin = [(ex, rv) for ex, rv in acc if rv.rating != "fair"]
    acc_pos = [p for p in acc_bin if p[1].rating in taxonomy.POSITIVE]
    acc_neg = [p for p in acc_bin if p[1].rating in taxonomy.NEGATIVE]
    fn = [(ex, rv) for ex, rv in rej if rv.rating in taxonomy.POSITIVE]
    hours = _captured_hours(db)
    starts, ends, durations, ideal = [], [], [], []
    for ex, rv in acc:
        ai = ex.ai or {}
        if rv.short_start is not None and ai.get("start") is not None:
            starts.append(abs(ai["start"] - rv.short_start))
        if rv.short_end is not None and ai.get("end") is not None:
            ends.append(abs(ai["end"] - rv.short_end))
        if ai.get("start") is not None and ai.get("end") is not None:
            durations.append(ai["end"] - ai["start"])
        if rv.short_start is not None and rv.short_end is not None:
            ideal.append(rv.short_end - rv.short_start)
    reasons: dict[str, int] = defaultdict(int)
    for _, rv in pairs:
        for r in rv.editorial_reasons or []:
            reasons[r] += 1
    tech: dict[str, int] = defaultdict(int)
    for _, rv in pairs:
        for r in rv.technical_issues or []:
            tech[r] += 1
    n_pos_known = len(acc_pos) + len(fn)
    return {
        "reviewed": len(pairs), "reviewed_accepted": len(acc), "reviewed_rejected": len(rej),
        "approval_rate": round(len(acc_pos) / len(acc), 3) if acc else None,
        "precision": round(len(acc_pos) / len(acc_bin), 3) if acc_bin else None,
        "coverage": round(len(acc_pos) / n_pos_known, 3) if n_pos_known else None,
        "false_negatives_known": len(fn),
        "false_positives": len(acc_neg),
        "false_positives_per_hour": round(len(acc_neg) / hours, 3) if hours and acc else None,
        "duplicates_flagged": reasons.get("duplicate", 0),
        "start_error_s": {"median": _pct(starts, 0.5), "p95": _pct(starts, 0.95), "n": len(starts)},
        "end_error_s": {"median": _pct(ends, 0.5), "p95": _pct(ends, 0.95), "n": len(ends)},
        "duration_s": {"ai_mean": round(statistics.mean(durations), 1) if durations else None,
                       "human_ideal_mean": round(statistics.mean(ideal), 1) if ideal else None},
        "precision_at_5": {r: precision_at_k(pairs, r, 5) for r in ("heuristic", "contextual", "ranker")},
        "editorial_reasons": dict(reasons), "technical_issues": dict(tech),
        "hours_captured": round(hours, 2),
        "false_negative_examples": [{"example_id": ex.id, "stratum": ex.stratum, "score": (ex.ai or {}).get("score"),
                                     "decision": (ex.ai or {}).get("decision"), "rating": rv.rating}
                                    for ex, rv in fn[:50]],
        "false_positive_examples": [{"example_id": ex.id, "clip_id": ex.clip_id, "score": (ex.ai or {}).get("score"),
                                     "rating": rv.rating, "reasons": rv.editorial_reasons}
                                    for ex, rv in acc_neg[:50]],
    }


def _captured_hours(db: Session) -> float:
    total = 0.0
    for (cov,) in db.execute(select(StreamSession.covered_until)):
        total += float(cov or 0)
    return total / 3600.0


def operational(db: Session, hours: int = 24) -> dict:
    """Latency per stage (queue wait + run), time to a ready Short, queue state, candidates/hour."""
    from datetime import timedelta

    from ..db import utcnow
    from ..models import Candidate

    since = utcnow() - timedelta(hours=hours)
    stages: dict[str, dict] = {}
    for jtype in ("audio_analyze", "score", "vision_analyze", "render", "dataset_media"):
        rows = db.execute(select(Job.created_at, Job.started_at, Job.finished_at).where(
            Job.type == jtype, Job.status == "done", Job.finished_at >= since)).all()
        run = [(f - s).total_seconds() for c, s, f in rows if s and f]
        wait = [(s - c).total_seconds() for c, s, f in rows if s and c]
        stages[jtype] = {"n": len(rows), "run_p50": _pct(run, 0.5), "run_p95": _pct(run, 0.95),
                         "wait_p50": _pct(wait, 0.5), "wait_p95": _pct(wait, 0.95)}
    ready = db.execute(select(Clip.created_at, Clip.updated_at, Clip.analysis_meta).where(
        Clip.status.in_(("ready", "approved")), Clip.is_test.is_(False), Clip.created_at >= since)).all()
    to_ready = [(u - c).total_seconds() for c, u, _ in ready if u and c]
    vlm = [float((m or {}).get("timings", {}).get("vlm_s")) for _, _, m in ready
           if (m or {}).get("timings", {}).get("vlm_s") is not None]
    queued = dict(db.execute(select(Job.type, func.count()).where(Job.status == "queued").group_by(Job.type)).all())
    oldest = {t: (utcnow() - c).total_seconds() for t, c in db.execute(
        select(Job.type, func.min(Job.created_at)).where(Job.status == "queued").group_by(Job.type)).all() if c}
    n_cand = db.scalar(select(func.count()).select_from(Candidate).where(Candidate.created_at >= since)) or 0
    n_acc = db.scalar(select(func.count()).select_from(Candidate).where(Candidate.created_at >= since,
                                                                        Candidate.decision == "accepted")) or 0
    return {
        "window_hours": hours, "stages": stages,
        "clip_ready_s": {"p50": _pct(to_ready, 0.5), "p95": _pct(to_ready, 0.95), "n": len(to_ready)},
        "vlm_s": {"p50": _pct(vlm, 0.5), "p95": _pct(vlm, 0.95), "n": len(vlm)},
        "queue": {"queued": queued, "oldest_s": {k: round(v) for k, v in oldest.items()}},
        "candidates_per_hour": round(n_cand / hours, 1), "clips_per_hour": round(n_acc / hours, 2),
    }
