"""HTS internal panel for the commercial operation (/api/admin/customers, /api/admin/channels, ...). Admin only.

Shows customers, plans and consumption, connected destinations WITHOUT secrets, publications and failures; lets the
HTS block a customer's automatic publishing, give or revoke courtesy plans (separate from payments), see how many
customers follow each streamer, decide whether the HTS also monitors it, block the republication of a streamer, and
see capacity. Every change is audited.
"""

from __future__ import annotations

from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .. import benefits, courtesy, customer_publish as cp, entitlements
from ..db import get_db, utcnow
from ..models import (AccountSubscription, AuditLog, ChannelMonitor, Clip, ClipShare, CourtesyGrant, Job, MonitorSession, Publication,
                      PublishAccount, StreamSession, Streamer, User)
from ..publish.service import PLATFORM_NAME
from .deps import CurrentUser, audit, require_admin

router = APIRouter(prefix="/api/admin", tags=["admin-customers"])


def _iso(dt):
    return dt.isoformat() + "Z" if dt else None


def _customer(db: Session, uid: int) -> User:
    u = db.get(User, uid)
    if u is None or u.role != "customer":
        raise HTTPException(404, "cliente não encontrado")
    return u


def _plan(db: Session, uid: int) -> dict | None:
    ent = entitlements.current(db, uid)
    if ent is None:
        return None
    return {"plan": ent.plan, "provider": ent.provider, "status": ent.status, "until": _iso(ent.until),
            "courtesy": ent.provider == "hts", "review": ent.review}


@router.get("/customers")
def customers(db: Session = Depends(get_db), _: CurrentUser = Depends(require_admin)):
    day_start = utcnow() - timedelta(days=1)
    out = []
    for u in db.scalars(select(User).where(User.role == "customer").order_by(User.created_at.desc()).limit(500)):
        s = cp.settings_for(db, u.id)
        out.append({
            "id": u.id, "email": u.email, "active": u.active, "created_at": _iso(u.created_at),
            "plan": _plan(db, u.id), "usage": entitlements.usage_summary(db, u.id),
            "destinations": len(cp.destinations(db, u.id)),
            "automation": {"enabled": bool(s and s.enabled), "blocked": bool(s and s.blocked_reason)},
            "failures_24h": db.scalar(select(func.count()).select_from(Publication).where(
                Publication.owner_id == u.id, Publication.status == "failed", Publication.updated_at >= day_start)) or 0,
        })
    return out


@router.get("/customers/{uid}")
def customer(uid: int, db: Session = Depends(get_db), _: CurrentUser = Depends(require_admin)):
    u = _customer(db, uid)
    s = cp.settings_for(db, uid)
    subs = db.scalars(select(AccountSubscription).where(AccountSubscription.user_id == uid)
                      .order_by(AccountSubscription.updated_at.desc()))
    monitors = []
    for m in db.scalars(select(ChannelMonitor).where(ChannelMonitor.user_id == uid, ChannelMonitor.removed_at.is_(None))):
        st = db.get(Streamer, m.streamer_id)
        monitors.append({"id": m.id, "channel": (st.display_name or st.channel) if st else "", "platform": st.platform if st else "",
                         "preset": m.preset, "paused_reason": m.paused_reason})
    pubs = db.scalars(select(Publication).where(Publication.owner_id == uid).order_by(Publication.id.desc()).limit(100))
    return {
        "id": u.id, "email": u.email, "active": u.active, "plan": _plan(db, uid),
        "subscriptions": [{"provider": r.provider, "plan": r.plan, "access_status": r.access_status,
                           "financial_status": r.financial_status, "review": r.review, "until": _iso(r.access_until)}
                          for r in subs],
        "grants": [courtesy.view(g) for g in db.scalars(select(CourtesyGrant).where(CourtesyGrant.user_id == uid)
                                                         .order_by(CourtesyGrant.id.desc()))],
        # paid plan, benefit in effect and dates; credits and temporary upgrades (history kept, revoked included)
        "access": benefits.summary(db, uid), "benefits": benefits.history(db, uid),
        "usage": entitlements.usage_summary(db, uid), "publishing_today": cp.summary(db, uid),
        "automation": {"enabled": bool(s and s.enabled), "terms_accepted": bool(s and s.terms_accepted_at),
                       "timezone": s.timezone if s else None, "blocked_reason": s.blocked_reason if s else "",
                       "blocked_by": s.blocked_by if s else ""},
        "monitors": monitors,
        # destinations without any token, scope list or secret
        "destinations": [{"id": a.id, "platform": a.platform, "platform_name": PLATFORM_NAME.get(a.platform, a.platform),
                          "name": a.name, "handle": a.handle, "status": a.status, "paused_reason": a.paused_reason,
                          "connected_at": _iso(a.connected_at)} for a in cp.destinations(db, uid)],
        "publications": [{"id": p.id, "platform": p.platform, "status": p.status, "title": p.title,
                          "scheduled_at": _iso(p.scheduled_at), "published_at": _iso(p.published_at),
                          "error": p.last_error[:300], "url": p.remote_url} for p in pubs],
    }


class BlockIn(BaseModel):
    reason: str = Field(..., min_length=5, max_length=200)


@router.post("/customers/{uid}/publishing/block")
def block(uid: int, body: BlockIn, request: Request, db: Session = Depends(get_db), admin: CurrentUser = Depends(require_admin)):
    _customer(db, uid)
    s = cp.settings_for(db, uid, create=True)
    s.blocked_reason, s.blocked_by = body.reason.strip(), admin.email
    waiting = 0
    for p in db.scalars(select(Publication).where(Publication.owner_id == uid, Publication.status == "scheduled")):
        cp.defer(db, p, f"bloqueado pela HTS: {body.reason.strip()}")
        waiting += 1
    audit(db, request, admin, "admin.customer_publish_block", f"user:{uid}", {"reason": body.reason.strip(), "held": waiting})
    db.commit()
    return {"ok": True, "held": waiting}


@router.post("/customers/{uid}/publishing/unblock")
def unblock(uid: int, request: Request, db: Session = Depends(get_db), admin: CurrentUser = Depends(require_admin)):
    _customer(db, uid)
    s = cp.settings_for(db, uid, create=True)
    previous = s.blocked_reason
    s.blocked_reason, s.blocked_by = "", ""
    audit(db, request, admin, "admin.customer_publish_unblock", f"user:{uid}", {"previous": previous})
    db.commit()
    return {"ok": True}


class GrantIn(BaseModel):
    plan: str = Field(..., pattern="^(basic|creator|pro)$")
    days: int = Field(..., ge=1, le=courtesy.MAX_DAYS)
    reason: str = Field(..., min_length=5, max_length=300)


@router.post("/customers/{uid}/grants", status_code=201)
def give(uid: int, body: GrantIn, request: Request, db: Session = Depends(get_db), admin: CurrentUser = Depends(require_admin)):
    try:
        g = courtesy.grant(db, user_id=uid, plan=body.plan, days=body.days, reason=body.reason, admin=admin.email)
    except courtesy.GrantError as exc:
        raise HTTPException(422, str(exc)) from exc
    audit(db, request, admin, "admin.courtesy_grant", f"user:{uid}",
          {"grant": g.id, "plan": body.plan, "days": body.days, "reason": body.reason})
    db.commit()
    return courtesy.view(g)


class RevokeIn(BaseModel):
    reason: str = Field(..., min_length=5, max_length=300)


@router.post("/customers/{uid}/grants/{gid}/revoke")
def revoke(uid: int, gid: int, body: RevokeIn, request: Request, db: Session = Depends(get_db),
           admin: CurrentUser = Depends(require_admin)):
    g = db.get(CourtesyGrant, gid)
    if g is None or g.user_id != uid:
        raise HTTPException(404, "cortesia não encontrada")
    try:
        courtesy.revoke(db, gid, reason=body.reason, admin=admin.email)
    except courtesy.GrantError as exc:
        raise HTTPException(422, str(exc)) from exc
    audit(db, request, admin, "admin.courtesy_revoke", f"user:{uid}", {"grant": gid, "reason": body.reason})
    db.commit()
    return courtesy.view(g)


# ------------------------------------------------------------------ access credits and temporary upgrades
REQUEST_KEY = r"^[A-Za-z0-9_-]{16,64}$"


class CreditPreviewIn(BaseModel):
    days: int = Field(..., ge=1, le=benefits.MAX_CREDIT_DAYS)


class CreditIn(CreditPreviewIn):
    reason: str = Field(..., min_length=5, max_length=300)
    request_key: str = Field(..., pattern=REQUEST_KEY)


class UpgradeIn(BaseModel):
    plan: str = Field(..., pattern="^(creator|pro)$")
    days: int = Field(..., ge=1, le=benefits.MAX_UPGRADE_DAYS)
    reason: str = Field(..., min_length=5, max_length=300)
    request_key: str = Field(..., pattern=REQUEST_KEY)


@router.post("/customers/{uid}/credits/preview")
def credit_preview(uid: int, body: CreditPreviewIn, db: Session = Depends(get_db), _: CurrentUser = Depends(require_admin)):
    """Resulting access before confirming. Changes nothing."""
    _customer(db, uid)
    try:
        return benefits.preview_credit(db, uid, body.days)
    except benefits.BenefitError as exc:
        raise HTTPException(422, str(exc)) from exc


@router.post("/customers/{uid}/credits", status_code=201)
def credit(uid: int, body: CreditIn, request: Request, db: Session = Depends(get_db), admin: CurrentUser = Depends(require_admin)):
    """Access credit (compensation days). Never touches payments; the next charge keeps its date."""
    _customer(db, uid)
    try:
        row, created = benefits.add_credit(db, user_id=uid, days=body.days, reason=body.reason,
                                           request_key=body.request_key, admin=admin.email)
    except benefits.BenefitError as exc:
        raise HTTPException(422, str(exc)) from exc
    if created:
        audit(db, request, admin, "admin.access_credit", f"user:{uid}", {"benefit": row.id, "days": row.days, "reason": row.reason})
    db.commit()
    return {**benefits.view(row), "duplicate": not created, "access": benefits.summary(db, uid)}


@router.post("/customers/{uid}/upgrades", status_code=201)
def upgrade(uid: int, body: UpgradeIn, request: Request, db: Session = Depends(get_db), admin: CurrentUser = Depends(require_admin)):
    """Temporary upgrade: a higher plan's limits for a few days, then the paid plan again. Same period, same price."""
    _customer(db, uid)
    try:
        row, created = benefits.add_upgrade(db, user_id=uid, plan=body.plan, days=body.days, reason=body.reason,
                                            request_key=body.request_key, admin=admin.email)
    except benefits.BenefitError as exc:
        raise HTTPException(422, str(exc)) from exc
    if created:
        audit(db, request, admin, "admin.temporary_upgrade", f"user:{uid}",
              {"benefit": row.id, "plan": row.plan, "days": row.days, "reason": row.reason})
    db.commit()
    return {**benefits.view(row), "duplicate": not created, "access": benefits.summary(db, uid)}


@router.post("/customers/{uid}/benefits/{bid}/revoke")
def revoke_benefit(uid: int, bid: int, body: RevokeIn, request: Request, db: Session = Depends(get_db),
                   admin: CurrentUser = Depends(require_admin)):
    _customer(db, uid)
    try:
        row = benefits.revoke(db, user_id=uid, benefit_id=bid, reason=body.reason, admin=admin.email)
    except benefits.BenefitError as exc:
        raise HTTPException(404 if "não encontrado" in str(exc) else 422, str(exc)) from exc
    audit(db, request, admin, "admin.benefit_revoke", f"user:{uid}", {"benefit": bid, "kind": row.kind, "reason": body.reason})
    db.commit()
    return benefits.view(row)


HISTORY_ACTIONS = ("admin.courtesy_grant", "admin.courtesy_revoke", "admin.access_credit", "admin.temporary_upgrade",
                   "admin.benefit_revoke", "admin.customer_publish_block", "admin.customer_publish_unblock")


@router.get("/customers/{uid}/history")
def customer_history(uid: int, db: Session = Depends(get_db), _: CurrentUser = Depends(require_admin)):
    """Audited administrative changes of this customer's access (who, when, what, why)."""
    _customer(db, uid)
    rows = db.scalars(select(AuditLog).where(AuditLog.target == f"user:{uid}", AuditLog.action.in_(HISTORY_ACTIONS))
                      .order_by(AuditLog.id.desc()).limit(200))
    return [{"id": r.id, "action": r.action, "by": r.user_email, "at": _iso(r.created_at), "details": r.details or {}}
            for r in rows]


@router.get("/channels")
def channels(db: Session = Depends(get_db), _: CurrentUser = Depends(require_admin)):
    counts = dict(db.execute(select(ChannelMonitor.streamer_id, func.count()).where(ChannelMonitor.removed_at.is_(None))
                             .group_by(ChannelMonitor.streamer_id)).all())
    rows = []
    for s in db.scalars(select(Streamer).order_by(Streamer.id)):
        followers = int(counts.get(s.id, 0))
        if followers == 0 and not s.hts_monitoring:
            continue
        rows.append({"id": s.id, "platform": s.platform, "channel": s.channel, "display_name": s.display_name,
                     "status": s.status, "enabled": s.enabled, "hts_monitoring": s.hts_monitoring, "followers": followers,
                     "customer_publish_blocked": bool((s.preferences or {}).get("customer_publish_blocked"))})
    return rows


class ChannelPatch(BaseModel):
    hts_monitoring: bool | None = None
    customer_publish_blocked: bool | None = None
    reason: str = Field("", max_length=200)


@router.patch("/channels/{sid}")
def patch_channel(sid: int, body: ChannelPatch, request: Request, db: Session = Depends(get_db),
                  admin: CurrentUser = Depends(require_admin)):
    s = db.get(Streamer, sid)
    if s is None:
        raise HTTPException(404, "canal não encontrado")
    changes = {}
    if body.hts_monitoring is not None:
        s.hts_monitoring = body.hts_monitoring  # never affects customers following it
        changes["hts_monitoring"] = body.hts_monitoring
    if body.customer_publish_blocked is not None:
        if body.customer_publish_blocked and len(body.reason.strip()) < 5:
            raise HTTPException(422, "informe o motivo do bloqueio de republicação")
        prefs = dict(s.preferences or {})
        prefs["customer_publish_blocked"] = body.customer_publish_blocked
        prefs["customer_publish_blocked_reason"] = body.reason.strip() if body.customer_publish_blocked else ""
        s.preferences = prefs
        changes["customer_publish_blocked"] = body.customer_publish_blocked
        if body.customer_publish_blocked:  # publications not started yet are cancelled for every customer
            for p in db.scalars(select(Publication).join(Clip, Clip.id == Publication.clip_id).where(
                    Clip.streamer_id == sid, Publication.owner_id.is_not(None),
                    Publication.status.in_(("proposed", "waiting", "scheduled")))):
                cp.cancel(db, p, "a republicação deste canal foi bloqueada pela HTS")
    audit(db, request, admin, "admin.channel_update", f"streamer:{sid}", {**changes, "reason": body.reason.strip()})
    db.commit()
    return {"ok": True, **changes}


class PlatformsIn(BaseModel):
    platforms: list[str] = Field(default_factory=list, max_length=8)


@router.get("/customer-publish-platforms")
def get_platforms(db: Session = Depends(get_db), _: CurrentUser = Depends(require_admin)):
    return {"enabled": cp.enabled_platforms(db), "known": list(cp.KNOWN_PLATFORMS), "never_automatic": list(cp.NEVER_AUTOMATIC)}


@router.put("/customer-publish-platforms")
def put_platforms(body: PlatformsIn, request: Request, db: Session = Depends(get_db),
                  admin: CurrentUser = Depends(require_admin)):
    """Turn a platform on for customers only after a real, homologated upload on it."""
    enabled = cp.set_enabled_platforms(db, body.platforms)
    audit(db, request, admin, "admin.customer_publish_platforms", "platforms", {"enabled": enabled})
    db.commit()
    return {"enabled": enabled}


@router.get("/capacity")
def capacity(db: Session = Depends(get_db), _: CurrentUser = Depends(require_admin)):
    jobs = dict(db.execute(select(Job.type, func.count()).where(Job.status.in_(("queued", "running"))).group_by(Job.type)).all())
    return {
        "live_sessions": db.scalar(select(func.count()).select_from(StreamSession).where(StreamSession.status == "live")) or 0,
        "customers_following_now": db.scalar(select(func.count(func.distinct(MonitorSession.user_id))).where(
            MonitorSession.state == "watching")) or 0,
        "customers": db.scalar(select(func.count()).select_from(User).where(User.role == "customer")) or 0,
        "customer_library_bytes": db.scalar(select(func.coalesce(func.sum(ClipShare.video_bytes), 0)).where(
            ClipShare.monitor_id.is_not(None), ClipShare.revoked_at.is_(None))) or 0,
        "jobs_pending": jobs,
        "customer_publications_open": db.scalar(select(func.count()).select_from(Publication).where(
            Publication.owner_id.is_not(None), Publication.status.in_(cp.OPEN))) or 0,
        "customer_destinations": db.scalar(select(func.count()).select_from(PublishAccount).where(
            PublishAccount.owner_id.is_not(None), PublishAccount.status != "revoked")) or 0,
    }
