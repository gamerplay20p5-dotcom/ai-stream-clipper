"""Customer entitlements: which plan an account has right now, what it has consumed and what it may start.

Source of truth split:
* the commercial service (``commercial/``) owns the money: checkout, provider webhooks, ledger, refunds. It
  sends a full **entitlement snapshot** per subscription through a transactional outbox (HMAC, at-least-once);
* this module owns **access**: ``apply_snapshot`` stores the snapshot idempotently (event id) and in order
  (outbox sequence); ``current`` answers "is this account entitled now, to which limits";
* the pipeline asks the ``check_*`` / ``meter_*`` helpers before and during work. A channel is SHARED: many
  customers follow it (``ChannelMonitor``) and one capture and one base analysis serve all of them. Each customer
  is attached to a live (``MonitorSession``) only within its own plan, consumes its own hours and receives its own
  library; one customer running out never stops the others. Channels the HTS consumes itself
  (``streamers.hts_monitoring``) are never limited: behaviour unchanged.

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
from .models import (AccountSubscription, BillingEvent, ChannelMonitor, Clip, ClipShare, MonitorSession, StreamSession,
                     Streamer, UsageEntry, User)

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
    """Bytes in the customer's own library. A file shared physically with other customers still counts for each
    library that holds it, so following a channel someone else already follows never frees quota."""
    return int(db.scalar(select(func.coalesce(func.sum(ClipShare.video_bytes), 0)).where(
        ClipShare.user_id == user_id, ClipShare.monitor_id.is_not(None), ClipShare.revoked_at.is_(None))) or 0)


def live_sessions(db: Session, user_id: int, exclude_session: int | None = None) -> int:
    q = (select(func.count()).select_from(MonitorSession).join(StreamSession, StreamSession.id == MonitorSession.session_id)
         .where(MonitorSession.user_id == user_id, MonitorSession.state == "watching", StreamSession.status == "live"))
    if exclude_session is not None:
        q = q.where(MonitorSession.session_id != exclude_session)
    return int(db.scalar(q) or 0)


def channel_count(db: Session, user_id: int) -> int:
    return int(db.scalar(select(func.count()).select_from(ChannelMonitor).where(
        ChannelMonitor.user_id == user_id, ChannelMonitor.removed_at.is_(None))) or 0)


def usage_summary(db: Session, user_id: int) -> dict:
    ent = current(db, user_id)
    out = {"channels": channel_count(db, user_id), "storage_bytes": storage_bytes(db, user_id),
           "live_sessions": live_sessions(db, user_id), "live_seconds": 0, "exports_4k": 0}
    if ent is not None:
        out["live_seconds"] = used(db, ent, "live_seconds")
        out["exports_4k"] = used(db, ent, "export_4k")
    return out


# ---------------------------------------------------------------------------
# shared lives: who consumes a live session
# ---------------------------------------------------------------------------


def _lock_account(db: Session, ent: Entitlement) -> None:
    """Serialize quota decisions of one account (row lock on PostgreSQL; SQLite serializes writers anyway)."""
    db.scalar(select(AccountSubscription.id).where(AccountSubscription.id == ent.subscription_id).with_for_update())


def lock_accounts(db: Session, user_ids) -> None:
    """Lock the subscription rows of several accounts in ONE deterministic order (by id). Every path that decides
    quotas for many customers at once (segments, attachments) takes them this way first, so two workers handling
    the same live can never deadlock on PostgreSQL. Re-locking a row already held is a no-op."""
    ids = sorted(set(user_ids))
    if ids:
        db.scalars(select(AccountSubscription.id).where(AccountSubscription.user_id.in_(ids))
                   .order_by(AccountSubscription.id).with_for_update()).all()


def active_monitors(db: Session, streamer_id: int) -> list[ChannelMonitor]:
    return list(db.scalars(select(ChannelMonitor).where(ChannelMonitor.streamer_id == streamer_id,
                                                        ChannelMonitor.removed_at.is_(None))
                           .order_by(ChannelMonitor.created_at, ChannelMonitor.id)))


def attach_block_reason(db: Session, monitor: ChannelMonitor, ent: Entitlement | None, session_id: int | None) -> str:
    """'' when this customer may follow a live of the channel now, otherwise why not (shown to that customer only)."""
    if ent is None:
        return "assinatura inativa"
    if monitor.paused_reason:
        return monitor.paused_reason
    if used(db, ent, "live_seconds") >= ent.limits["hours"] * 3600:
        return "franquia de horas do ciclo esgotada"
    if live_sessions(db, ent.user_id, exclude_session=session_id) >= ent.limits["simultaneous"]:
        return "limite de lives simultâneas do plano"
    if storage_bytes(db, ent.user_id) >= ent.limits["storage_gb"] * GB:
        return "armazenamento do plano cheio"
    return ""


def attachments(db: Session, session_id: int, state: str | None = None) -> list[MonitorSession]:
    q = select(MonitorSession).where(MonitorSession.session_id == session_id)
    if state:
        q = q.where(MonitorSession.state == state)
    return list(db.scalars(q.order_by(MonitorSession.id)))


def _stop(att: MonitorSession, at: float, reason: str) -> None:
    att.state, att.until_t, att.stop_reason, att.stopped_at = "stopped", at, reason[:120], utcnow()


def attach_consumers(db: Session, sess: StreamSession, from_t: float = 0.0) -> int:
    """Attach every customer that follows the channel and may follow this live now. Idempotent: a customer is
    attached at most once per session (unique monitor+session). A refusal is recorded and retried on the next
    segment (the customer may renew meanwhile); a customer that consumed part of the live and stopped stays stopped."""
    added = 0
    monitors = active_monitors(db, sess.streamer_id)
    lock_accounts(db, [m.user_id for m in monitors])
    for m in monitors:
        att = db.scalar(select(MonitorSession).where(MonitorSession.monitor_id == m.id, MonitorSession.session_id == sess.id))
        if att is not None and att.state != "refused":
            continue
        ent = current(db, m.user_id)
        reason = attach_block_reason(db, m, ent, sess.id)
        try:
            with db.begin_nested():
                if att is None:
                    att = MonitorSession(monitor_id=m.id, user_id=m.user_id, session_id=sess.id, from_t=from_t)
                    db.add(att)
                if reason:
                    att.state, att.stop_reason, att.from_t, att.until_t = "refused", reason[:120], from_t, from_t
                else:
                    att.state, att.stop_reason, att.from_t, att.until_t, att.started_at = "watching", "", from_t, None, utcnow()
                db.flush()
        except IntegrityError:
            continue  # a concurrent request attached this customer first (unique monitor+session): keep theirs
        if not reason:
            added += 1
    return added


def check_session_start(db: Session, streamer: Streamer) -> str:
    """'' when a live session may start: the HTS consumes the channel, or at least one following customer may."""
    if streamer.hts_monitoring:
        return ""
    monitors = active_monitors(db, streamer.id)
    if not monitors:
        return "nenhum assinante acompanha este canal"
    reasons = []
    for m in monitors:
        reason = attach_block_reason(db, m, current(db, m.user_id), None)
        if not reason:
            return ""
        reasons.append(reason)
    return f"nenhum assinante pode acompanhar agora ({reasons[0]})"


def session_stop_reason(db: Session, streamer: Streamer | None, sess: StreamSession) -> str:
    """Checked on every capture poll: the shared capture continues while the HTS or any customer still follows it."""
    if streamer is None or not streamer.enabled:
        return "canal desativado"
    if streamer.hts_monitoring:
        return ""
    if attachments(db, sess.id, "watching"):
        return ""
    return "nenhum assinante acompanhando esta live"


def meter_segment(db: Session, streamer: Streamer, sess: StreamSession, idx: int, start: float, seconds: float) -> bool:
    """One new captured segment: every attached customer is charged once for it (``live:{user}:{session}:{idx}``)
    within its own franchise. A customer that runs out stops here; the others continue. Returns False when nobody
    (HTS or customer) needs this segment, so it is not analysed."""
    attach_consumers(db, sess, from_t=start)
    watching = attachments(db, sess.id, "watching")
    lock_accounts(db, [a.user_id for a in watching])
    for att in watching:
        monitor = db.get(ChannelMonitor, att.monitor_id)
        if monitor is None or monitor.removed_at is not None:
            _stop(att, start, "canal removido pelo assinante")
            continue
        if monitor.paused_reason:
            _stop(att, start, monitor.paused_reason)
            continue
        ent = current(db, att.user_id)
        if ent is None:
            _stop(att, start, "assinatura inativa")
            continue
        _lock_account(db, ent)
        limit = ent.limits["hours"] * 3600
        if used(db, ent, "live_seconds") >= limit:
            _stop(att, start, "franquia de horas do ciclo esgotada")
            continue
        # The segment that crosses the limit is still delivered to this customer; it stops right after it
        record_usage(db, att.user_id, "live_seconds", f"live:{att.user_id}:{sess.id}:{idx}", ent.period_key,
                     max(0, round(seconds)))
        if used(db, ent, "live_seconds") >= limit:
            _stop(att, start + seconds, "franquia de horas do ciclo esgotada")
    db.flush()
    if streamer.hts_monitoring:
        return True
    return any(a.state == "watching" or (a.until_t is not None and a.until_t > start)
               for a in attachments(db, sess.id) if a.state != "refused")


# Segment boundaries and clip bounds are floats from different workers: half a second absorbs rounding only
COVERAGE_TOLERANCE_S = 0.5


def covers(att: MonitorSession, start: float, end: float) -> bool:
    """The WHOLE moment lies inside the part of the live this customer followed (and paid hours for)."""
    if att.state == "refused":
        return False
    if start < att.from_t - COVERAGE_TOLERANCE_S:
        return False  # starts before the customer joined
    return att.until_t is None or end <= att.until_t + COVERAGE_TOLERANCE_S  # ends after it stopped


def consumers_of_clip(db: Session, clip: Clip) -> list[MonitorSession]:
    """Customers whose followed window contains the entire clip: only they may receive it."""
    return [a for a in attachments(db, clip.session_id) if covers(a, clip.start, clip.end)]


_REVOKED = ("refund", "chargeback", "dispute")


def delivery_entitlement(db: Session, user_id: int) -> Entitlement | None:
    """Entitlement that governs delivering a moment the customer ALREADY followed (hours charged while access was
    valid). The current one when access is valid; otherwise the latest subscription, so a clip captured during
    the paid period and rendered after the end is still delivered. Never after a refund, a chargeback or while the
    payment is under review."""
    ent = current(db, user_id)
    if ent is not None:
        return ent
    row = db.scalar(select(AccountSubscription).where(AccountSubscription.user_id == user_id)
                    .order_by(AccountSubscription.updated_at.desc(), AccountSubscription.id.desc()).limit(1))
    if row is None or row.review or any(word in (row.financial_status or "") for word in _REVOKED):
        return None
    if not _valid_limits(row.limits):
        return None
    anchor = row.period_start or row.period_end or row.access_until or row.updated_at
    return Entitlement(row.user_id, row.id, row.provider, row.plan, row.access_status, row.access_until or anchor,
                       f"{row.provider}:{row.subscription_ref}:{int(anchor.replace(tzinfo=timezone.utc).timestamp())}",
                       dict(row.limits or {}), row.review or "")


def render_block_reason(db: Session, streamer: Streamer, clip: Clip | None = None) -> str:
    """'' when the (shared) render may run: the HTS needs it, or a customer entitled to this moment has room."""
    if streamer.hts_monitoring or clip is None:
        return ""
    reason = "nenhum assinante neste trecho da live"
    for att in consumers_of_clip(db, clip):
        ent = delivery_entitlement(db, att.user_id)
        if ent is None:
            reason = "assinatura inativa"
        elif storage_bytes(db, att.user_id) >= ent.limits["storage_gb"] * GB:
            reason = "armazenamento do plano cheio"
        else:
            return ""
    return reason


def has_feature(db: Session, streamer: Streamer | None, name: str, clip: Clip | None = None) -> bool:
    """A premium step (second pass, variants, priority) runs on shared work only when someone needs it: the HTS,
    or a customer entitled to this moment whose plan includes the feature. It never runs once per customer."""
    if streamer is None or streamer.hts_monitoring:
        return True
    if clip is not None:
        consumers = consumers_of_clip(db, clip)
    else:
        live = db.scalar(select(StreamSession.id).where(StreamSession.streamer_id == streamer.id,
                                                        StreamSession.status == "live").order_by(StreamSession.id.desc()))
        consumers = attachments(db, live, "watching") if live else []
    for att in consumers:
        ent = current(db, att.user_id)
        if ent is not None and ent.feature(name):
            return True
    return False


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


PLAN_PAUSE = "pausado: fora do limite do plano"


def enforce_account(db: Session, user_id: int) -> None:
    """Apply the account's current entitlement right away (after every snapshot), for THIS customer only:
    monitors beyond the plan are paused (the oldest stay active) and come back when the plan allows; without
    access, or when paused, its attachments to running lives stop. Shared channels and captures are never
    touched here: other customers and the HTS keep going. Clips already in the library are kept."""
    ent = current(db, user_id)
    monitors = db.scalars(select(ChannelMonitor).where(ChannelMonitor.user_id == user_id, ChannelMonitor.removed_at.is_(None))
                          .order_by(ChannelMonitor.created_at, ChannelMonitor.id)).all()
    allowed = int(ent.limits["channels"]) if ent else 0
    for position, m in enumerate(monitors):
        if ent is not None and position >= allowed:
            m.paused_reason = PLAN_PAUSE
        elif m.paused_reason == PLAN_PAUSE:
            m.paused_reason = ""
    paused = {m.id for m in monitors if m.paused_reason}
    for att in db.scalars(select(MonitorSession).where(MonitorSession.user_id == user_id, MonitorSession.state == "watching")):
        if ent is None or att.monitor_id in paused:
            sess = db.get(StreamSession, att.session_id)
            _stop(att, sess.covered_until if sess else 0.0, "assinatura inativa" if ent is None else PLAN_PAUSE)
