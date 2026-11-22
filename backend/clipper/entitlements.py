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
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .db import utcnow
from .models import (AccessBenefit, AccountSubscription, BillingEvent, ChannelMonitor, Clip, ClipShare, EntitlementPeriod, MonitorSession,
                     Segment, StreamSession, Streamer, UsageEntry, User)

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
        if row.access_status in ENTITLED and row.access_until is not None:
            # what an HTS access credit extends after a natural end (a shorter date after a refund lowers it too)
            row.last_entitled_until = row.access_until
            row.last_period_anchor = row.period_start or row.period_end or row.access_until
        elif row.last_entitled_until is not None and row.last_entitled_until > row.updated_at:
            # ended before the date it had (immediate cancellation, expiry): access stopped NOW, so a credit can
            # never give back the rest of that period
            row.last_entitled_until = row.updated_at
        outcome = "applied"
    try:
        with db.begin_nested():
            db.add(BillingEvent(event_id=event_id, provider=provider, subscription_ref=ref, seq=seq, outcome=outcome))
            db.flush()
    except IntegrityError:
        return "duplicate"  # a concurrent delivery of the same event won
    if outcome == "applied":
        enforce_account(db, user.id)
        # a resolved review, a won dispute or a renewal may release deliveries that were waiting
        from . import editorial

        editorial.redeliver_for_user(db, user.id)
        from . import customer_publish

        customer_publish.enforce_destinations(db, user.id)  # downgrade/upgrade: destinations beyond the plan pause
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
    base_plan: str = ""  # the subscription's own plan (paid or courtesy), without any temporary upgrade
    credit_days: int = 0  # HTS access credit days counted in ``until``
    upgrade_until: datetime | None = None  # end of the temporary upgrade in effect, if any

    def feature(self, name: str) -> bool:
        return bool((self.limits.get("features") or {}).get(name))


# A paid subscription that ended this way may still run its HTS access credits (never after a refund, a chargeback,
# a suspension, an administrative cancellation or an open review)
NATURAL_END = {"stripe": {"canceled"}, "infinitepay": {"expired"}}


def _refunded(row: AccountSubscription) -> bool:
    return (any(isinstance(p, dict) and p.get("status") == "refunded" for p in (row.payments or []))
            or any(word in (row.financial_status or "") for word in ("refund", "chargeback"))
            or row.review == "dispute_lost")


def _base_until(row: AccountSubscription) -> datetime | None:
    """Until when this subscription's own access goes (before credits), or None when it grants nothing."""
    if row.access_status in ENTITLED and row.access_until is not None:
        return row.access_until
    if (row.provider in NATURAL_END and row.access_status == "ended" and row.financial_status in NATURAL_END[row.provider]
            and not row.review and not _refunded(row)):
        return row.last_entitled_until
    return None


def credit_days(db: Session, user_id: int) -> int:
    return int(db.scalar(select(func.coalesce(func.sum(AccessBenefit.days), 0)).where(
        AccessBenefit.user_id == user_id, AccessBenefit.kind == "credit", AccessBenefit.revoked_at.is_(None))) or 0)


def active_upgrade(db: Session, user_id: int, now: datetime | None = None) -> AccessBenefit | None:
    now = now or utcnow()
    rows = db.scalars(select(AccessBenefit).where(
        AccessBenefit.user_id == user_id, AccessBenefit.kind == "upgrade", AccessBenefit.revoked_at.is_(None),
        AccessBenefit.starts_at <= now, AccessBenefit.ends_at > now)).all()
    return max(rows, key=lambda b: (PLAN_RANK.get(b.plan, 0), b.ends_at), default=None)


def current(db: Session, user_id: int, now: datetime | None = None, extra_credit_days: int = 0) -> Entitlement | None:
    """The account's entitlement right now: the best subscription whose access has not ended, with the HTS benefits.

    * HTS access credits (``access_benefits`` kind ``credit``) add days after the access of the latest paid
      subscription (also after a natural end; never after a refund, suspension or during a review). They move with
      every renewal, so renewing never loses them. ``extra_credit_days`` only previews a credit not given yet.
    * a temporary upgrade (kind ``upgrade``) applies a higher plan's limits while it lasts, inside the SAME period
      (same period key): consumption is never reset and access is never extended by it."""
    from . import catalog

    now = now or utcnow()
    rows = db.scalars(select(AccountSubscription).where(AccountSubscription.user_id == user_id)).all()
    bases = {r.id: _base_until(r) for r in rows}
    paid = [r for r in rows if r.provider != "hts" and bases[r.id] is not None]
    credit = (credit_days(db, user_id) + max(0, int(extra_credit_days))) if paid else 0
    target = max(paid, key=lambda r: bases[r.id], default=None)
    valid = []
    for r in rows:
        until = bases[r.id]
        if until is None:
            continue
        days = credit if (target is not None and r.id == target.id and not r.review) else 0
        until = until + timedelta(days=days)
        if until > now:
            own = r.access_status in ENTITLED and r.access_until is not None and r.access_until > now
            valid.append((r, until, r.access_status if own else "active", days))
    if not valid:
        return None
    # Precedence: the highest plan wins; on the same plan a PAID subscription wins over an HTS courtesy
    # (provider "hts"), then the one that lasts longer. Courtesy rows never alter paid snapshots.
    best, until, status, days = max(valid, key=lambda v: (PLAN_RANK.get(v[0].plan, 0), v[0].provider != "hts", v[1]))
    anchor = best.period_start or best.period_end or best.access_until or best.last_period_anchor or best.last_entitled_until
    plan, limits, up_until = best.plan, dict(best.limits or {}), None
    up = active_upgrade(db, user_id, now)
    if up is not None and PLAN_RANK.get(up.plan, 0) > PLAN_RANK.get(best.plan, 0):
        plan, limits, up_until = up.plan, catalog.limits_for(up.plan), up.ends_at
    return Entitlement(best.user_id, best.id, best.provider, plan, status, until,
                       f"{best.provider}:{best.subscription_ref}:{int(anchor.replace(tzinfo=timezone.utc).timestamp())}",
                       limits, best.review or "", best.plan, days, up_until)


# ---------------------------------------------------------------------------
# consumption
# ---------------------------------------------------------------------------


def used(db: Session, ent: Entitlement, metric: str) -> int:
    return int(db.scalar(select(func.coalesce(func.sum(UsageEntry.amount), 0)).where(
        UsageEntry.user_id == ent.user_id, UsageEntry.metric == metric, UsageEntry.period_key == ent.period_key)) or 0)


def record_usage(db: Session, user_id: int, metric: str, source_key: str, period_key: str, amount: int,
                 period_id: int | None = None) -> bool:
    """Meter one unit. False when that source was already counted (retries, duplicates, reconnects)."""
    try:
        with db.begin_nested():
            db.add(UsageEntry(user_id=user_id, metric=metric, source_key=source_key[:160], period_key=period_key,
                              amount=int(amount), period_id=period_id))
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
                     max(0, round(seconds)), period_id=period_for(db, ent).id)
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


def period_for(db: Session, ent: Entitlement) -> EntitlementPeriod:
    """The paid period (subscription, plan, limits) current consumption is charged to. One row per account,
    period and plan: a plan change inside a period starts a new row, so each hour keeps the plan it was paid under."""
    q = select(EntitlementPeriod).where(EntitlementPeriod.user_id == ent.user_id, EntitlementPeriod.period_key == ent.period_key,
                                        EntitlementPeriod.plan == ent.plan)
    row = db.scalar(q)
    if row is not None:
        return row
    sub = db.get(AccountSubscription, ent.subscription_id)
    try:
        with db.begin_nested():
            row = EntitlementPeriod(user_id=ent.user_id, subscription_id=ent.subscription_id, period_key=ent.period_key,
                                    plan=ent.plan, limits=dict(ent.limits), period_start=sub.period_start if sub else None,
                                    period_end=sub.period_end if sub else None)
            db.add(row)
            db.flush()
        return row
    except IntegrityError:
        return db.scalar(q)  # recorded concurrently


def _epoch(value: datetime | None) -> int | None:
    return int(value.replace(tzinfo=timezone.utc).timestamp()) if value else None


_REFUNDED = ("refund", "chargeback")


def period_state(db: Session, period: EntitlementPeriod) -> str:
    """Financial state of ONE paid period, from its subscription's latest snapshot:

    * ``revoked``: that period was refunded in full (refund, chargeback, dispute lost). No new deliveries from it.
    * ``hold``: the subscription is under financial review or an open dispute. Deliveries wait until it is resolved
      (a later snapshot releases them, see ``editorial.redeliver_for_user``).
    * ``ok``: paid; cancellation, expiry and operational suspension do not change what was already paid."""
    sub = db.get(AccountSubscription, period.subscription_id)
    if sub is None:
        return "revoked"
    anchor = sub.period_start or sub.period_end or sub.access_until
    is_current = anchor is not None and period.period_key == f"{sub.provider}:{sub.subscription_ref}:{_epoch(anchor)}"
    refunded_now = any(word in (sub.financial_status or "") for word in _REFUNDED) or sub.review == "dispute_lost"
    if refunded_now and not is_current:
        # A full refund concerns the latest paid period of the subscription (the refund snapshot may carry other
        # period dates): the most recent period recorded for it is revoked too, older renewed periods are not
        latest = db.scalar(select(func.max(EntitlementPeriod.period_start)).where(
            EntitlementPeriod.subscription_id == sub.id))
        is_current = latest is not None and period.period_start is not None and period.period_start >= latest
    if refunded_now and is_current:
        return "revoked"
    start, end = _epoch(period.period_start), _epoch(period.period_end)
    for payment in sub.payments or []:
        at = payment.get("at") if isinstance(payment, dict) else None
        if payment.get("status") == "refunded" and isinstance(at, (int, float)) and start is not None and end is not None \
                and start <= at < end:
            return "revoked"  # e.g. one InfinitePay month refunded: only that month
    if sub.review and sub.review != "dispute_lost":
        return "hold"
    return "ok"


@dataclass(frozen=True)
class Rights:
    state: str  # ok | hold | no
    period: EntitlementPeriod | None = None
    reason: str = ""


def delivery_rights(db: Session, att: MonitorSession, clip: Clip) -> Rights:
    """May this customer receive this clip now? Judged by the periods that PAID for the moment, never by a later
    or current subscription:

    1. the whole clip lies in the customer's followed window (``covers``);
    2. the account is not blocked (security: ``users.active``);
    3. every captured segment under the clip was charged to this customer (no rights over hours never paid);
    4. none of those periods was refunded (``revoked``) and none is under review/dispute (``hold``: retried later).
    The plan, preset and limits applied are those of the period of the clip's first segment."""
    if not covers(att, clip.start, clip.end):
        return Rights("no", reason="fora do trecho acompanhado")
    user = db.get(User, att.user_id)
    if user is None or not user.active:
        return Rights("no", reason="conta bloqueada")
    segs = db.scalars(select(Segment).where(
        Segment.session_id == clip.session_id, Segment.start < clip.end - COVERAGE_TOLERANCE_S,
        Segment.start + Segment.duration > clip.start + COVERAGE_TOLERANCE_S).order_by(Segment.start)).all()
    if not segs:
        return Rights("no", reason="trecho sem captura")
    keys = {f"live:{att.user_id}:{clip.session_id}:{s.idx}": s.start for s in segs}
    entries = db.scalars(select(UsageEntry).where(UsageEntry.metric == "live_seconds",
                                                  UsageEntry.source_key.in_(list(keys)))).all()
    if len(entries) < len(keys) or any(e.user_id != att.user_id or e.period_id is None for e in entries):
        return Rights("no", reason="trecho não pago por este assinante")
    periods = {e.period_id: db.get(EntitlementPeriod, e.period_id) for e in entries}
    states = {pid: (period_state(db, p) if p else "revoked") for pid, p in periods.items()}
    first = periods[min(entries, key=lambda e: keys[e.source_key]).period_id]
    if "revoked" in states.values():
        return Rights("no", first, "período estornado")
    if "hold" in states.values():
        return Rights("hold", first, "pagamento em revisão")
    return Rights("ok", first)


def render_block_reason(db: Session, streamer: Streamer, clip: Clip | None = None) -> str:
    """'' when the (shared) render may run: the HTS needs it, or a customer entitled to this moment has room."""
    if streamer.hts_monitoring or clip is None:
        return ""
    reason = "nenhum assinante neste trecho da live"
    for att in consumers_of_clip(db, clip):
        rights = delivery_rights(db, att, clip)
        if rights.state == "no":
            reason = rights.reason
        elif storage_bytes(db, att.user_id) >= rights.period.limits["storage_gb"] * GB:
            reason = "armazenamento do plano cheio"
        else:
            return ""  # ok, or held by a review that may be resolved: render once, deliver when released
    return reason


def has_feature(db: Session, streamer: Streamer | None, name: str, clip: Clip | None = None) -> bool:
    """A premium step (second pass, variants, priority) runs on shared work only when someone needs it: the HTS,
    or a customer entitled to this moment whose plan includes the feature. It never runs once per customer."""
    if streamer is None or streamer.hts_monitoring:
        return True
    if clip is not None:
        # the plan of the period that paid for this moment, not today's subscription
        for att in consumers_of_clip(db, clip):
            rights = delivery_rights(db, att, clip)
            if rights.state != "no" and (rights.period.limits.get("features") or {}).get(name):
                return True
        return False
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
