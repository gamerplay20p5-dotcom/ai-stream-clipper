"""Plans the HTS gives (courtesy, trial, partnership), with validity and reason.

Kept apart from payments: each grant is its own ``account_subscriptions`` row with provider ``hts`` (never
``stripe`` / ``infinitepay``), financial status ``courtesy`` and the catalog limits of the plan. Paid snapshots,
the commercial service and its ledger are never touched. Precedence (``entitlements.current``): the highest plan
wins; on the same plan the paid subscription wins. Every action is audited by the caller.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from . import catalog, customer_publish, editorial, entitlements
from .db import utcnow
from .models import AccountSubscription, CourtesyGrant, User

MAX_DAYS = 365


class GrantError(ValueError):
    pass


def grant(db: Session, *, user_id: int, plan: str, days: int, reason: str, admin: str) -> CourtesyGrant:
    user = db.get(User, user_id)
    if user is None or user.role != "customer":
        raise GrantError("cortesias só podem ser dadas a contas de cliente")
    if plan not in catalog.PLANS:
        raise GrantError("plano desconhecido")
    if not 1 <= int(days) <= MAX_DAYS:
        raise GrantError(f"validade entre 1 e {MAX_DAYS} dias")
    if len((reason or "").strip()) < 5:
        raise GrantError("informe o motivo da cortesia")
    now = utcnow()
    ends = now + timedelta(days=int(days))
    g = CourtesyGrant(user_id=user_id, plan=plan, starts_at=now, ends_at=ends, reason=reason.strip()[:300],
                      created_by=admin)
    db.add(g)
    db.flush()
    sub = AccountSubscription(user_id=user_id, provider="hts", subscription_ref=f"courtesy:{g.id}", plan=plan, market="",
                              financial_status="courtesy", review="", access_status="active", access_until=ends,
                              period_start=now, period_end=ends, renews_at=None, cancel_at_period_end=True,
                              limits=catalog.limits_for(plan), manage="hts", renewal=None, payments=[], last_seq=0,
                              updated_at=now)
    db.add(sub)
    db.flush()
    g.subscription_id = sub.id
    _apply(db, user_id)
    return g


def revoke(db: Session, grant_id: int, *, reason: str, admin: str) -> CourtesyGrant:
    g = db.get(CourtesyGrant, grant_id)
    if g is None:
        raise GrantError("cortesia não encontrada")
    if g.revoked_at is not None:
        return g
    if len((reason or "").strip()) < 5:
        raise GrantError("informe o motivo da revogação")
    now = utcnow()
    g.revoked_at, g.revoked_by, g.revoke_reason = now, admin, reason.strip()[:300]
    sub = db.get(AccountSubscription, g.subscription_id) if g.subscription_id else None
    if sub is not None:
        sub.access_status, sub.access_until, sub.updated_at = "ended", now, now
    _apply(db, g.user_id)
    return g


def _apply(db: Session, user_id: int) -> None:
    db.flush()
    entitlements.enforce_account(db, user_id)
    editorial.redeliver_for_user(db, user_id)
    customer_publish.enforce_destinations(db, user_id)


def view(g: CourtesyGrant) -> dict:
    iso = lambda d: d.replace(tzinfo=timezone.utc).isoformat() if isinstance(d, datetime) else None  # noqa: E731
    active = g.revoked_at is None and g.ends_at > utcnow()
    return {"id": g.id, "plan": g.plan, "starts_at": iso(g.starts_at), "ends_at": iso(g.ends_at), "reason": g.reason,
            "created_by": g.created_by, "revoked_at": iso(g.revoked_at), "revoked_by": g.revoked_by,
            "revoke_reason": g.revoke_reason, "active": active}
