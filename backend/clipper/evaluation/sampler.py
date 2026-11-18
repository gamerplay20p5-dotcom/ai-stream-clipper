"""Active learning: decide which scored moments are worth a human review.

Reviewing every minute of a live is impossible, so the sampler keeps, per session and hour:
every automatic clip, moments close to the threshold, rejected moments with a real reaction
(possible false negatives), game noise without reaction, and small deterministic samples of
ordinary conversation and low scores (negatives the system must keep rejecting).
Selection is deterministic (hash of the candidate) so reruns produce the same dataset.
"""

from __future__ import annotations

import hashlib

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..models import Candidate, EvalExample

NEAR_MARGIN = 12.0  # score points around min_score
HOUR = 3600.0

QUOTA = {  # max examples per session per rolling hour of stream time
    "near_threshold": 3,
    "rejected_reaction": 3,
    "noise_no_reaction": 1,
    "conversation": 1,
    "low_score": 1,
}
SAMPLE_ONE_IN = {"conversation": 6, "low_score": 4}  # deterministic subsampling of plentiful strata


def _gate(cand_id: int, one_in: int) -> bool:
    return int(hashlib.sha1(f"cand:{cand_id}".encode()).hexdigest()[:6], 16) % one_in == 0


def stratum_of(c: Candidate, min_score: float) -> tuple[str, float] | None:
    """Return (stratum, priority) or None. Priority: higher = more informative to label."""
    f = c.features or {}
    if c.decision == "accepted":
        return None  # clips are added by the pipeline when the clip is created
    score = c.score if c.score is not None else f.get("score")
    reaction = (f.get("n_laughter", 0) or f.get("n_scream", 0) or f.get("n_silence_break", 0)
                or f.get("cat_surprise", 0) or (f.get("yam_laughter") or 0) >= 0.3)
    if c.decision in ("below_min_score", "duplicate", "rate_limit") and score is not None \
            and score >= min_score - NEAR_MARGIN:
        return "near_threshold", 90.0 - abs(score - min_score) * 2.5
    if c.decision != "text_only" and reaction:
        return "rejected_reaction", 70.0
    if f.get("game_only") or (f.get("uncorroborated") and max(f.get("yam_gunfire") or 0, f.get("yam_game_impact") or 0) >= 0.3):
        return "noise_no_reaction", 50.0
    if c.decision == "text_only":
        return ("conversation", 20.0) if _gate(c.id, SAMPLE_ONE_IN["conversation"]) else None
    if c.decision == "below_min_score":
        return ("low_score", 25.0) if _gate(c.id, SAMPLE_ONE_IN["low_score"]) else None
    return None


def quota_left(db: Session, c: Candidate, stratum: str, per_hour_total: int) -> bool:
    lo = c.peak - HOUR
    q = select(func.count()).select_from(EvalExample).join(Candidate, EvalExample.candidate_id == Candidate.id).where(
        Candidate.session_id == c.session_id, Candidate.peak > lo, Candidate.peak <= c.peak)
    if db.scalar(q.where(EvalExample.stratum == stratum)) >= QUOTA.get(stratum, 1):
        return False
    return db.scalar(q.where(EvalExample.stratum != "accepted")) < per_hour_total


def select_for_review(db: Session, cands: list[Candidate], min_score: float, per_hour_total: int) -> list[tuple]:
    """Pick candidates (non-accepted) for the dataset. Returns [(candidate, stratum, priority)]."""
    out = []
    for c in cands:
        st = stratum_of(c, min_score)
        if st is None:
            continue
        if quota_left(db, c, st[0], per_hour_total):
            out.append((c, st[0], st[1]))
    return out
