"""Per-customer editorial selection on top of the SHARED base analysis.

One live produces one set of candidate moments (shared capture, transcription, visual analysis, scoring and
render). Each customer then receives, in its own library, the moments that match its editorial preset and fall
inside the part of the live its plan covered (``entitlements.consumers_of_clip``). Nothing here runs a model: a
preset only re-weights the criteria the base analysis already computed (``Clip.score_breakdown``), so choosing a
preset never costs CPU/GPU and never changes what other customers receive.

Delivery is an ordinary ``ClipShare`` tagged with ``monitor_id``: authorization per account in the backend, the
media snapshot of that moment, and cleanup protection while it is shared (``storage_lifecycle.protection``). The
physical file is the same for every customer that receives the clip.
"""

from __future__ import annotations

import logging

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from . import entitlements
from .models import ChannelMonitor, Clip, ClipShare

log = logging.getLogger(__name__)

DELIVERABLE = ("ready", "approved")
DEFAULT_PRESET = "balanced"
# weights over Clip.score_breakdown (0-10 each); None = the base score as computed by the shared analysis
PRESETS: dict[str, dict] = {
    "balanced": {"weights": None, "bonus": 0.0},
    "best": {"weights": None, "bonus": 10.0},  # only the strongest moments
    "humor": {"weights": {"humor": 0.45, "reaction": 0.35, "audience": 0.2}, "bonus": 0.0},
    "highlights": {"weights": {"surprise": 0.35, "visual": 0.3, "intensity": 0.2, "uniqueness": 0.15}, "bonus": 0.0},
    "intense": {"weights": {"intensity": 0.5, "reaction": 0.3, "surprise": 0.2}, "bonus": 0.0},
}


def allowed_presets(ent: entitlements.Entitlement | None) -> tuple[str, ...]:
    """Basic uses the base selection; plans with editing (Creator, Pro) choose a preset."""
    if ent is not None and ent.feature("editing"):
        return tuple(PRESETS)
    return (DEFAULT_PRESET,)


def effective_preset(monitor: ChannelMonitor, ent: entitlements.Entitlement | None) -> str:
    """The preset applied now: a preset the plan no longer includes falls back to the base selection."""
    return monitor.preset if monitor.preset in allowed_presets(ent) else DEFAULT_PRESET


def preset_score(clip: Clip, preset: str) -> float:
    """0-100. Half the base score (keeps its penalties: game-only noise, uncorroborated peaks...) and half the
    preset's criteria, so a preset changes the ranking without letting a weak moment through."""
    spec = PRESETS.get(preset) or PRESETS[DEFAULT_PRESET]
    base = float(clip.score or 0.0)
    weights = spec["weights"]
    if not weights:
        return base
    breakdown = clip.score_breakdown or {}
    num = den = 0.0
    for name, weight in weights.items():
        value = breakdown.get(name)
        if isinstance(value, (int, float)):
            num += weight * float(value)
            den += weight
    if den == 0:
        return base
    return 0.5 * base + 0.5 * 10.0 * num / den


def selects(clip: Clip, preset: str, min_score: float) -> bool:
    spec = PRESETS.get(preset) or PRESETS[DEFAULT_PRESET]
    return preset_score(clip, preset) >= min_score + spec["bonus"]


def deliver(db: Session, clip: Clip, min_score: float | None = None) -> int:
    """Put a rendered clip in the library of every customer whose followed window contains the WHOLE clip and
    whose preset selects it. Idempotent: never duplicates an active share; a customer whose storage is full does
    not receive it. Shares already delivered are never withdrawn here."""
    if clip.is_test or clip.status not in DELIVERABLE or not clip.video_path:
        return 0
    if min_score is None:
        from .settings_store import load_ai_settings

        min_score = load_ai_settings(db).min_score
    delivered = 0
    for att in entitlements.consumers_of_clip(db, clip):
        monitor = db.get(ChannelMonitor, att.monitor_id)
        if monitor is None or monitor.removed_at is not None:
            continue
        # the moment was followed while access was valid: delivered even if the clip is ready after the end,
        # never after a refund/chargeback or during a payment review
        ent = entitlements.delivery_entitlement(db, att.user_id)
        if ent is None:
            continue
        if not selects(clip, effective_preset(monitor, ent), min_score):
            continue
        if db.scalar(select(ClipShare.id).where(ClipShare.clip_id == clip.id, ClipShare.user_id == att.user_id,
                                                ClipShare.revoked_at.is_(None))):
            continue
        if entitlements.storage_bytes(db, att.user_id) + int(clip.file_size or 0) > ent.limits["storage_gb"] * entitlements.GB:
            log.info("clip not delivered: customer storage full", extra={"clip_id": clip.id, "user_id": att.user_id})
            continue
        from .sharing import snapshot

        try:
            with db.begin_nested():
                share = ClipShare(clip_id=clip.id, user_id=att.user_id, created_by=None, can_view=True, can_download=True,
                                  can_variants=bool(ent.feature("variants")), monitor_id=monitor.id)
                snapshot(share, clip)
                db.add(share)
                db.flush()
        except IntegrityError:
            continue  # delivered concurrently (one active share per clip and account, enforced by the database)
        delivered += 1
    return delivered
