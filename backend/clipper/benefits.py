"""HTS access benefits on top of a subscription: access credits (compensation days) and temporary upgrades.

Access only, never money: nothing here touches payments, invoices, the commercial service or the paid snapshots
(``account_subscriptions`` rows sent by Stripe/InfinitePay). The effect is computed by ``entitlements.current``:

* **access credit** (``kind='credit'``): N days added after the access of the customer's latest paid subscription.
  The next charge is NOT postponed (Stripe and InfinitePay keep their dates); the credit moves with every renewal,
  so a renewal never loses it. It also runs after a natural end (Stripe canceled, InfinitePay expired), never after
  a refund, a chargeback, a suspension or while a payment is under review.
* **temporary upgrade** (``kind='upgrade'``): the limits of a higher plan until ``ends_at``, then the paid plan again
  automatically. Consumption stays in the same period (same period key), so quotas are never reset; it never extends
  access and never revives a suspended, refunded or expired subscription (it needs a current entitlement).
* **courtesy plans** stay in ``courtesy.py`` (own subscription row, provider ``hts``).

Every operation is idempotent by ``request_key`` (a repeated click returns the same benefit), serialized per account
(the account's subscription rows are locked) and audited by the caller. Revocation keeps the row (history).
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from . import catalog, customer_publish, editorial, entitlements
from .db import utcnow
from .models import AccessBenefit, AccountSubscription, User

MAX_CREDIT_DAYS = 90
MAX_UPGRADE_DAYS = 90
REQUEST_KEY = re.compile(r"^[A-Za-z0-9_-]{16,64}$")


class BenefitError(ValueError):
    pass


def _iso(d: datetime | None) -> str | None:
    return d.replace(tzinfo=timezone.utc).isoformat() if isinstance(d, datetime) else None


def _customer(db: Session, user_id: int) -> User:
    user = db.get(User, user_id)
    if user is None or user.role != "customer":
        raise BenefitError("benefícios só podem ser dados a contas de cliente")
    return user


def _reason(reason: str) -> str:
    text = (reason or "").strip()
    if len(text) < 5:
        raise BenefitError("informe o motivo")
    return text[:300]


def _existing(db: Session, request_key: str, user_id: int, kind: str) -> AccessBenefit | None:
    if not REQUEST_KEY.match(request_key or ""):
        raise BenefitError("pedido sem identificador válido")
    row = db.scalar(select(AccessBenefit).where(AccessBenefit.request_key == request_key))
    if row is not None and (row.user_id != user_id or row.kind != kind):
        raise BenefitError("identificador de pedido já usado em outra operação")
    return row


def _lock(db: Session, user_id: int) -> None:
    """Serialize benefit decisions of one account (its subscription rows; SQLite serializes writers anyway)."""
    entitlements.lock_accounts(db, [user_id])


def _insert(db: Session, row: AccessBenefit) -> tuple[AccessBenefit, bool]:
    try:
        with db.begin_nested():
            db.add(row)
            db.flush()
        return row, True
    except IntegrityError:
        # the same request arrived twice at the same time: the first one wins, the second returns it
        return db.scalar(select(AccessBenefit).where(AccessBenefit.request_key == row.request_key)), False


def _apply(db: Session, user_id: int) -> None:
    db.flush()
    entitlements.enforce_account(db, user_id)
    editorial.redeliver_for_user(db, user_id)
    customer_publish.enforce_destinations(db, user_id)


def _paid_rows(db: Session, user_id: int) -> list[AccountSubscription]:
    return [r for r in db.scalars(select(AccountSubscription).where(AccountSubscription.user_id == user_id,
                                                                   AccountSubscription.provider != "hts"))]


# ------------------------------------------------------------------ access credits
def preview_credit(db: Session, user_id: int, days: int) -> dict:
    """What a credit of ``days`` would do, without giving it: current and resulting access dates."""
    _customer(db, user_id)
    if not 1 <= int(days) <= MAX_CREDIT_DAYS:
        raise BenefitError(f"entre 1 e {MAX_CREDIT_DAYS} dias")
    now = utcnow()
    before = entitlements.current(db, user_id, now)
    after = entitlements.current(db, user_id, now, extra_credit_days=int(days))
    applies = after is not None and after.credit_days > (before.credit_days if before else 0)
    reason = ""
    if not _paid_rows(db, user_id):
        reason = "o cliente não tem assinatura paga: use uma cortesia"
    elif after is None or not applies:
        reason = "a assinatura paga está suspensa, estornada, em revisão ou terminou há mais tempo que o crédito"
    return {"days": int(days), "applies": applies and not reason, "reason": reason,
            "before": {"plan": before.plan, "until": _iso(before.until)} if before else None,
            "after": {"plan": after.plan, "until": _iso(after.until)} if after else None,
            "paid_plan": (after or before).base_plan if (after or before) else None}


def add_credit(db: Session, *, user_id: int, days: int, reason: str, request_key: str, admin: str) -> tuple[AccessBenefit, bool]:
    """Gives an access credit. Returns (benefit, created): a repeated request returns the first one unchanged."""
    _customer(db, user_id)
    existing = _existing(db, request_key, user_id, "credit")
    if existing is not None:
        return existing, False
    text = _reason(reason)
    _lock(db, user_id)
    check = preview_credit(db, user_id, days)
    if not check["applies"]:
        raise BenefitError(check["reason"] or "o crédito não muda o acesso deste cliente")
    row, created = _insert(db, AccessBenefit(user_id=user_id, kind="credit", days=int(days), plan="", starts_at=utcnow(),
                                             ends_at=None, reason=text, request_key=request_key, created_by=admin))
    if created:
        _apply(db, user_id)
    return row, created


# ------------------------------------------------------------------ temporary upgrades
def add_upgrade(db: Session, *, user_id: int, plan: str, days: int, reason: str, request_key: str,
                admin: str) -> tuple[AccessBenefit, bool]:
    """A higher plan's limits for ``days`` (e.g. Creator -> Pro for 7 days), then back to the paid plan."""
    _customer(db, user_id)
    existing = _existing(db, request_key, user_id, "upgrade")
    if existing is not None:
        return existing, False
    if plan not in catalog.PLANS:
        raise BenefitError("plano desconhecido")
    if not 1 <= int(days) <= MAX_UPGRADE_DAYS:
        raise BenefitError(f"entre 1 e {MAX_UPGRADE_DAYS} dias")
    text = _reason(reason)
    _lock(db, user_id)
    now = utcnow()
    ent = entitlements.current(db, user_id, now)
    if ent is None:
        raise BenefitError("o cliente não tem um plano ativo para receber o upgrade (suspenso, estornado ou expirado)")
    if entitlements.PLAN_RANK[plan] <= entitlements.PLAN_RANK.get(ent.base_plan or ent.plan, 0):
        raise BenefitError("escolha um plano acima do plano atual do cliente")
    if entitlements.active_upgrade(db, user_id, now) is not None:
        raise BenefitError("já existe um upgrade temporário ativo: revogue-o antes de dar outro")
    row, created = _insert(db, AccessBenefit(user_id=user_id, kind="upgrade", days=int(days), plan=plan, starts_at=now,
                                             ends_at=now + timedelta(days=int(days)), reason=text,
                                             request_key=request_key, created_by=admin))
    if created:
        _apply(db, user_id)
    return row, created


# ------------------------------------------------------------------ revocation and history
def revoke(db: Session, *, user_id: int, benefit_id: int, reason: str, admin: str) -> AccessBenefit:
    row = db.get(AccessBenefit, benefit_id)
    if row is None or row.user_id != user_id:
        raise BenefitError("benefício não encontrado")
    if row.revoked_at is not None:
        return row
    text = _reason(reason)
    _lock(db, user_id)
    row.revoked_at, row.revoked_by, row.revoke_reason = utcnow(), admin, text
    _apply(db, user_id)
    return row


def view(row: AccessBenefit) -> dict:
    now = utcnow()
    if row.revoked_at is not None:
        state = "revoked"
    elif row.kind == "upgrade":
        state = "active" if row.ends_at and row.ends_at > now else "ended"
    else:
        state = "active"
    return {"id": row.id, "kind": row.kind, "days": row.days, "plan": row.plan, "starts_at": _iso(row.starts_at),
            "ends_at": _iso(row.ends_at), "reason": row.reason, "created_by": row.created_by,
            "created_at": _iso(row.created_at), "revoked_at": _iso(row.revoked_at), "revoked_by": row.revoked_by,
            "revoke_reason": row.revoke_reason, "state": state}


def history(db: Session, user_id: int) -> list[dict]:
    return [view(b) for b in db.scalars(select(AccessBenefit).where(AccessBenefit.user_id == user_id)
                                        .order_by(AccessBenefit.id.desc()))]


def summary(db: Session, user_id: int) -> dict:
    """Paid plan, benefit in effect and dates, for the HTS panel."""
    ent = entitlements.current(db, user_id)
    if ent is None:
        return {"entitled": False}
    sub = db.get(AccountSubscription, ent.subscription_id)
    return {"entitled": True, "plan": ent.plan, "paid_plan": ent.base_plan, "provider": ent.provider,
            "paid_until": _iso(sub.access_until if sub and sub.access_status in entitlements.ENTITLED else sub.last_entitled_until if sub else None),
            "access_until": _iso(ent.until), "credit_days": ent.credit_days, "upgrade_until": _iso(ent.upgrade_until)}


def enforce_boundaries(db: Session, window: timedelta = timedelta(minutes=20)) -> int:
    """Upgrades and credits end by time, not by an event: re-apply the plan of accounts whose benefit ended in the
    last ``window`` (monitors beyond the plan pause, destinations beyond it pause). Idempotent."""
    now = utcnow()
    users = set(db.scalars(select(AccessBenefit.user_id).where(
        AccessBenefit.kind == "upgrade", AccessBenefit.ends_at > now - window, AccessBenefit.ends_at <= now)))
    for uid in set(db.scalars(select(AccessBenefit.user_id).where(AccessBenefit.kind == "credit",
                                                                    AccessBenefit.revoked_at.is_(None)))):
        ent = entitlements.current(db, uid, now)
        ended_now = ent is None and entitlements.current(db, uid, now - window) is not None
        if ended_now or (ent is not None and ent.until <= now + window):
            users.add(uid)
    for uid in users:
        entitlements.enforce_account(db, uid)
        customer_publish.enforce_destinations(db, uid)
    return len(users)
