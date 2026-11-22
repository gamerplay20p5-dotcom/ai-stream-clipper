"""Customer entitlements: which plan an account has right now, what it has consumed and what it may start.

Source of truth split:
* the commercial service (``commercial/``) owns the money: checkout, provider webhooks, ledger, refunds. It
  sends a full **entitlement snapshot** per subscription through a transactional outbox (HMAC, at-least-once);
* this module owns **access**: ``apply_snapshot`` stores the snapshot idempotently (event id) and in order
  (outbox sequence); ``current`` answers "is this account entitled now, to which limits";
* the pipeline asks the ``check_*`` / ``meter_*`` helpers before and during work for channels owned by a
  customer. HTS channels (``owner_id IS NULL``) are never limited: behaviour unchanged.

Plan limits are NOT hard-coded here: they come inside each snapshot, from ``commercial/catalog.json``.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .db import utcnow
from .models import AccountSubscription, BillingEvent, Clip, StreamSession, Streamer, UsageEntry, User

log = logging.getLogger(__name__)

ENTITLED = ("active", "grace")
PLAN_RANK = {"basic": 1, "creator": 2, "pro": 3}
PROVIDERS = {"stripe", "infinitepay"}
ACCESS = {"active", "grace", "suspended", "ended", "none"}
GB = 1024 ** 3


class SnapshotRejected(ValueError):
    """Permanently invalid snapshot (HTTP 422): never applied, kept by the sender for the operator."""


class AccountNotReady(RuntimeError):
    """The account does not exist (yet) or is not a customer: the sender retries later (HTTP 409)."""


def _dt(epoch) -> datetime | None:
    if epoch in (None, 0, ""):
        return None
    return datetime.fromtimestamp(int(epoch), tz=timezone.utc).replace(tzinfo=None)


def _require(cond: bool, what: str) -> None:
    if not cond:
        raise SnapshotRejected(what)


def _valid_limits(limits) -> bool:
    if not isinstance(limits, dict):
        return False
    numbers = ("hours", "channels", "simultaneous", "storage_gb", "export_4k")
    return all(isinstance(limits.get(k), (int, float)) and limits[k] >= 0 for k in numbers) and isinstance(limits.get("features"), dict)


def apply_snapshot(db: Session, payload: dict) -> str:
    """Store one entitlement snapshot. Returns ``applied``, ``stale`` (older than what we have) or ``duplicate``.

    Safe under retries, duplicates, reordering and concurrent deliveries: the event id is recorded once
    and the subscription row is locked while its sequence is compared."""
    _require(isinstance(payload, dict), "payload")
    event_id = payload.get("event_id")
    provider = payload.get("provider")
    ref = payload.get("subscription_ref")
    seq = payload.get("seq")
    access = payload.get("access") or {}
    _require(isinstance(event_id, str) and 8 <= len(event_id) <= 64, "event_id")
    _require(provider in PROVIDERS, "provider")
    _require(isinstance(ref, str) and 0 < len(ref) <= 120, "subscription_ref")
    _require(isinstance(seq, int) and seq > 0, "seq")
    _require(isinstance(payload.get("account_id"), int) and payload["account_id"] > 0, "account_id")
    _require(payload.get("plan") in PLAN_RANK, "plan")
    _require(isinstance(access, dict) and access.get("status") in ACCESS, "access")
    _require(_valid_limits(payload.get("limits")), "limits")

    if db.get(BillingEvent, event_id) is not None:
        return "duplicate"
    user = db.get(User, payload["account_id"])
    if user is None or user.role != "customer":
        # Only customer accounts can hold subscriptions (never staff or share-only viewers)
        raise AccountNotReady("account")

    row = db.scalar(select(AccountSubscription).where(AccountSubscription.provider == provider,
                                                     AccountSubscription.subscription_ref == ref).with_for_update())
    if row is not None and row.user_id != user.id:
        raise SnapshotRejected("subscription belongs to another account")
    if row is not None and seq <= row.last_seq:
        outcome = "stale"
    else:
        if row is None:
            row = AccountSubscription(user_id=user.id, provider=provider, subscription_ref=ref)
            db.add(row)
        row.plan = payload["plan"]
        row.market = str(payload.get("market") or "")[:4]
        row.financial_status = str(payload.get("financial_status") or "")[:30]
        row.review = str(payload.get("review") or "")[:40]
        row.access_status = access["status"]
        row.access_until = _dt(access.get("until"))
        row.period_start = _dt(access.get("period_start"))
        row.period_end = _dt(access.get("period_end"))
        row.renews_at = _dt(access.get("renews_at"))
        row.cancel_at_period_end = bool(access.get("cancel_at_period_end"))
        row.limits = payload["limits"]
        row.manage = str(payload.get("manage") or "")[:30]
        renewal = payload.get("renewal")
        row.renewal = renewal if isinstance(renewal, dict) and str(renewal.get("url", "")).startswith("https://") else None
        row.payments = [p for p in (payload.get("payments") or []) if isinstance(p, dict)][:12]
        row.last_seq = seq
        row.updated_at = utcnow()
        outcome = "applied"
    try:
        with db.begin_nested():
            db.add(BillingEvent(event_id=event_id, provider=provider, subscription_ref=ref, seq=seq, outcome=outcome))
            db.flush()
    except IntegrityError:
        return "duplicate"  # a concurrent delivery of the same event won
    if outcome == "applied":
        enforce_account(db, user.id)
    return outcome


@dataclass(frozen=True)
class Entitlement:
    user_id: int
    subscription_id: int
    provider: str
    plan: str
    status: str  # active | grace
    until: datetime
    period_key: str
    limits: dict
    review: str

    def feature(self, name: str) -> bool:
        return bool((self.limits.get("features") or {}).get(name))


def current(db: Session, user_id: int, now: datetime | None = None) -> Entitlement | None:
    """The account's entitlement right now: the best active/grace subscription whose access has not ended."""
    now = now or utcnow()
    rows = db.scalars(select(AccountSubscription).where(AccountSubscription.user_id == user_id,
                                                       AccountSubscription.access_status.in_(ENTITLED))).all()
    valid = [r for r in rows if r.access_until is not None and r.access_until > now]
    if not valid:
        return None
    best = max(valid, key=lambda r: (PLAN_RANK.get(r.plan, 0), r.access_until))
    anchor = best.period_start or best.period_end or best.access_until
    return Entitlement(best.user_id, best.id, best.provider, best.plan, best.access_status, best.access_until,
                       f"{best.provider}:{best.subscription_ref}:{int(anchor.replace(tzinfo=timezone.utc).timestamp())}",
                       dict(best.limits or {}), best.review or "")


# ---------------------------------------------------------------------------
# consumption
# ---------------------------------------------------------------------------


def used(db: Session, ent: Entitlement, metric: str) -> int:
    return int(db.scalar(select(func.coalesce(func.sum(UsageEntry.amount), 0)).where(
        UsageEntry.user_id == ent.user_id, UsageEntry.metric == metric, UsageEntry.period_key == ent.period_key)) or 0)


def record_usage(db: Session, user_id: int, metric: str, source_key: str, period_key: str, amount: int) -> bool:
    """Meter one unit. False when that source was already counted (retries, duplicates, reconnects)."""
    try:
        with db.begin_nested():
            db.add(UsageEntry(user_id=user_id, metric=metric, source_key=source_key[:160], period_key=period_key,
                              amount=int(amount)))
            db.flush()
        return True
    except IntegrityError:
        return False


def storage_bytes(db: Session, user_id: int) -> int:
    return int(db.scalar(select(func.coalesce(func.sum(Clip.file_size), 0)).join(Streamer, Streamer.id == Clip.streamer_id)
                         .where(Streamer.owner_id == user_id)) or 0)


def live_sessions(db: Session, user_id: int) -> int:
    return int(db.scalar(select(func.count()).select_from(StreamSession).join(Streamer, Streamer.id == StreamSession.streamer_id)
                         .where(Streamer.owner_id == user_id, StreamSession.status == "live")) or 0)


def channel_count(db: Session, user_id: int) -> int:
    return int(db.scalar(select(func.count()).select_from(Streamer).where(Streamer.owner_id == user_id)) or 0)


def usage_summary(db: Session, user_id: int) -> dict:
    ent = current(db, user_id)
    out = {"channels": channel_count(db, user_id), "storage_bytes": storage_bytes(db, user_id),
           "live_sessions": live_sessions(db, user_id), "live_seconds": 0, "exports_4k": 0}
    if ent is not None:
        out["live_seconds"] = used(db, ent, "live_seconds")
        out["exports_4k"] = used(db, ent, "export_4k")
    return out


# ---------------------------------------------------------------------------
# checks used by the pipeline (customer channels only)
# ---------------------------------------------------------------------------


def _lock_account(db: Session, ent: Entitlement) -> None:
    """Serialize quota decisions of one account (row lock on PostgreSQL; SQLite serializes writers anyway)."""
    db.scalar(select(AccountSubscription.id).where(AccountSubscription.id == ent.subscription_id).with_for_update())


def check_session_start(db: Session, streamer: Streamer) -> str:
    """'' when a live session may start for this channel, otherwise the reason it may not."""
    if streamer.owner_id is None:
        return ""
    ent = current(db, streamer.owner_id)
    if ent is None:
        return "assinatura inativa"
    _lock_account(db, ent)
    if used(db, ent, "live_seconds") >= ent.limits["hours"] * 3600:
        return "franquia de horas do ciclo esgotada"
    if live_sessions(db, streamer.owner_id) >= ent.limits["simultaneous"]:
        return "limite de lives simultâneas do plano"
    if storage_bytes(db, streamer.owner_id) >= ent.limits["storage_gb"] * GB:
        return "armazenamento do plano cheio"
    return ""


def capture_stop_reason(db: Session, streamer: Streamer) -> str:
    """Checked on every capture poll: a running capture stops when access ends or the hours run out."""
    if streamer.owner_id is None:
        return ""
    ent = current(db, streamer.owner_id)
    if ent is None:
        return "assinatura inativa"
    if used(db, ent, "live_seconds") >= ent.limits["hours"] * 3600:
        return "franquia de horas do ciclo esgotada"
    return ""


def meter_segment(db: Session, streamer: Streamer, session_id: int, idx: int, seconds: float) -> bool:
    """Counts one captured segment against the cycle's hours. Returns False when it must NOT be analysed
    (no entitlement, or the franchise was already exhausted before this segment)."""
    if streamer.owner_id is None:
        return True
    ent = current(db, streamer.owner_id)
    if ent is None:
        return False
    _lock_account(db, ent)
    if used(db, ent, "live_seconds") >= ent.limits["hours"] * 3600:
        return False
    # The segment that crosses the limit is still analysed; the capture is told to stop right after
    record_usage(db, ent.user_id, "live_seconds", f"live:{session_id}:{idx}", ent.period_key, max(0, round(seconds)))
    return True


def render_block_reason(db: Session, streamer: Streamer) -> str:
    if streamer.owner_id is None:
        return ""
    ent = current(db, streamer.owner_id)
    if ent is None:
        return "assinatura inativa"
    if storage_bytes(db, streamer.owner_id) >= ent.limits["storage_gb"] * GB:
        return "armazenamento do plano cheio"
    return ""


def has_feature(db: Session, streamer: Streamer | None, name: str) -> bool:
    """Plan feature for a customer channel; HTS channels keep every feature (unchanged behaviour)."""
    if streamer is None or streamer.owner_id is None:
        return True
    ent = current(db, streamer.owner_id)
    return ent is not None and ent.feature(name)


def export_4k_block_reason(db: Session, user_id: int) -> str:
    """Why a 4K export may not run now ('' when allowed). Consumes nothing."""
    ent = current(db, user_id)
    if ent is None:
        return "assinatura inativa"
    if int(ent.limits.get("export_4k") or 0) <= 0:
        return "exportação 4K não incluída no plano"
    if used(db, ent, "export_4k") >= ent.limits["export_4k"]:
        return "exportações 4K do ciclo esgotadas"
    return ""


def reserve_export_4k(db: Session, user_id: int, clip_id: int) -> str:
    """Counts one 4K export for the cycle. '' on success (or if this clip was already counted), else the reason."""
    ent = current(db, user_id)
    if ent is None:
        return "assinatura inativa"
    if int(ent.limits.get("export_4k") or 0) <= 0:
        return "exportação 4K não incluída no plano"
    _lock_account(db, ent)
    if used(db, ent, "export_4k") >= ent.limits["export_4k"]:
        return "exportações 4K do ciclo esgotadas"
    record_usage(db, user_id, "export_4k", f"export4k:{ent.period_key}:{clip_id}", ent.period_key, 1)
    return ""


def enforce_account(db: Session, user_id: int) -> None:
    """Apply the account's current entitlement to running work right away (after every snapshot):
    no entitlement -> live captures of its channels are asked to stop; channels beyond the plan are paused
    (the oldest ones stay enabled). Clips already produced are kept."""
    ent = current(db, user_id)
    channels = db.scalars(select(Streamer).where(Streamer.owner_id == user_id).order_by(Streamer.created_at, Streamer.id)).all()
    allowed = int(ent.limits["channels"]) if ent else 0
    for position, streamer in enumerate(channels):
        over = position >= allowed
        if over and streamer.enabled:
            streamer.enabled = False
            streamer.last_error = "pausado: fora do limite do plano" if ent else "pausado: assinatura inativa"
        elif not over and not streamer.enabled and streamer.last_error.startswith("pausado: "):
            streamer.enabled = True
            streamer.last_error = ""
    if ent is None:
        for sess in db.scalars(select(StreamSession).join(Streamer, Streamer.id == StreamSession.streamer_id)
                               .where(Streamer.owner_id == user_id, StreamSession.status == "live")):
            sess.stop_requested = True
