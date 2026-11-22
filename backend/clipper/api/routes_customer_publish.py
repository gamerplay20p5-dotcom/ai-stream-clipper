"""Customer area "Publicação automática" (/api/account/publishing).

Everything is scoped to the signed-in customer: no route takes an owner id, every object is looked up with
``owner_id == user.id`` (another customer's object answers 404, like a missing one), tokens are never returned,
plan limits come from the entitlement. The HTS publishing panel (/api/publish/*) stays staff-only and separate.
"""

from __future__ import annotations

from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import customer_publish as cp
from .. import entitlements
from ..config import get_settings
from ..db import get_db, utcnow
from ..models import ChannelMonitor, Clip, Publication, PublishAccount, PublishRule, Streamer
from ..publish import service
from ..publish.service import PLATFORM_NAME, PublishRefused
from .deps import CurrentUser, audit
from .routes_account import customer_user

router = APIRouter(prefix="/api/account/publishing", tags=["customer-publishing"])


def _iso(dt):
    return dt.isoformat() + "Z" if dt else None


def _require_plan(db: Session, user: CurrentUser) -> dict:
    limits = cp.plan_limits(entitlements.current(db, user.id))
    if not limits["allowed"]:
        raise HTTPException(403, "publicação automática disponível nos planos Creator e Pro")
    return limits


def _own(db: Session, model, key: int, user: CurrentUser):
    obj = db.get(model, key)
    if obj is None or obj.owner_id != user.id:
        raise HTTPException(404, "não encontrado")
    return obj


def _account(a: PublishAccount) -> dict:
    return {"id": a.id, "platform": a.platform, "platform_name": PLATFORM_NAME.get(a.platform, a.platform),
            "name": a.name, "handle": a.handle, "status": a.status, "paused_reason": a.paused_reason,
            "privacy_options": (a.meta or {}).get("privacy_options") or [], "connected_at": _iso(a.connected_at),
            "needs_reconnect": a.status in ("expired", "error")}


def _rule(db: Session, r: PublishRule) -> dict:
    s = db.get(Streamer, r.streamer_id) if r.streamer_id else None
    return {"id": r.id, "account_id": r.account_id, "channel": (s.display_name or s.channel) if s else "",
            "monitor_streamer_id": r.streamer_id, "enabled": r.enabled, "require_approval": r.require_approval,
            "min_score": r.min_score, "privacy": r.privacy, "slots": r.slots or [],
            "rights_confirmed": r.rights_confirmed_at is not None, "rights_note": r.rights_note}


def _pub(db: Session, p: Publication) -> dict:
    clip = db.get(Clip, p.clip_id) if p.clip_id else None
    return {"id": p.id, "clip_id": p.clip_id, "title": p.title, "platform": p.platform,
            "platform_name": PLATFORM_NAME.get(p.platform, p.platform), "account_id": p.account_id, "status": p.status,
            "scheduled_at": _iso(p.scheduled_at), "published_at": _iso(p.published_at), "url": p.remote_url,
            "error": p.last_error if p.status in ("failed", "waiting") else "",
            "score": round(clip.score) if clip else None, "metrics": p.metrics or {}}


@router.get("")
def state(db: Session = Depends(get_db), user: CurrentUser = Depends(customer_user)):
    ent = entitlements.current(db, user.id)
    limits = cp.plan_limits(ent)
    s = cp.settings_for(db, user.id)
    gate, why = cp.gate(db, user.id)
    base = {"plan": ent.plan if ent else None, "allowed": limits["allowed"],
            "limits": {"destinations": limits["destinations"], "daily": limits["daily"]}}
    if not limits["allowed"]:
        return {**base, "upgrade": True}  # Basic: only the upgrade offer, nothing else
    pubs = list(db.scalars(select(Publication).where(Publication.owner_id == user.id,
                                                     Publication.created_at >= utcnow() - timedelta(days=30))
                           .order_by(Publication.id.desc()).limit(200)))
    monitors = db.scalars(select(ChannelMonitor).where(ChannelMonitor.user_id == user.id, ChannelMonitor.removed_at.is_(None)))
    channels = []
    for m in monitors:
        st = db.get(Streamer, m.streamer_id)
        channels.append({"streamer_id": m.streamer_id, "name": st.display_name or st.channel if st else ""})
    return {
        **base,
        "enabled": bool(s and s.enabled), "terms_accepted": bool(s and s.terms_accepted_at),
        "timezone": s.timezone if s else "America/Sao_Paulo", "blocked": bool(s and s.blocked_reason),
        "status": gate, "status_reason": why,
        "platforms": [{"id": p, "name": PLATFORM_NAME.get(p, p)} for p in cp.enabled_platforms(db)],
        "today": cp.summary(db, user.id),
        "accounts": [_account(a) for a in cp.destinations(db, user.id)],
        "rules": [_rule(db, r) for r in db.scalars(select(PublishRule).where(PublishRule.owner_id == user.id))],
        "channels": channels,
        "upcoming": [_pub(db, p) for p in pubs if p.status in ("proposed", "waiting", "scheduled", "uploading", "processing")],
        "history": [_pub(db, p) for p in pubs if p.status in ("published", "failed", "cancelled", "expired", "uncertain")][:50],
        "needs_action": [_pub(db, p) for p in pubs if p.status in ("proposed", "uncertain")
                         or (p.status == "failed" and "conect" in (p.last_error or ""))],
    }


class SettingsIn(BaseModel):
    enabled: bool | None = None
    timezone: str | None = Field(None, max_length=64)
    accept_terms: bool | None = None


@router.put("/settings")
def put_settings(body: SettingsIn, request: Request, db: Session = Depends(get_db),
                 user: CurrentUser = Depends(customer_user)):
    _require_plan(db, user)
    s = cp.settings_for(db, user.id, create=True)
    if s.blocked_reason:
        raise HTTPException(403, "a publicação automática desta conta foi bloqueada pela HTS")
    try:
        if body.timezone is not None:
            s.timezone = cp.validate_timezone(body.timezone)
    except cp.Refused as exc:
        raise HTTPException(422, str(exc)) from exc
    if body.accept_terms:
        s.terms_accepted_at = s.terms_accepted_at or utcnow()
    if body.enabled is not None:
        if body.enabled and s.terms_accepted_at is None:
            raise HTTPException(409, "aceite a autorização de publicação antes de ativar")
        s.enabled = body.enabled
    audit(db, request, user, "customer_publish.settings", f"user:{user.id}",
          {"enabled": s.enabled, "timezone": s.timezone, "terms": bool(s.terms_accepted_at)})
    db.commit()
    return {"enabled": s.enabled, "timezone": s.timezone, "terms_accepted": bool(s.terms_accepted_at)}


@router.post("/connect/{platform}")
def connect(platform: str, request: Request, db: Session = Depends(get_db), user: CurrentUser = Depends(customer_user)):
    """OAuth started by the OWNER of the destination; the account will belong to this customer only."""
    _require_plan(db, user)
    try:
        cp.can_connect(db, user.id, platform)
        out = service.start_oauth(db, platform, f"customer:{user.id}", get_settings().public_origin, owner_id=user.id)
    except cp.Refused as exc:
        raise HTTPException(409, str(exc)) from exc
    except PublishRefused as exc:
        raise HTTPException(409, "; ".join(exc.problems)) from exc
    audit(db, request, user, "customer_publish.oauth_start", platform)
    db.commit()
    return {"auth_url": out["auth_url"]}  # the state stays server-side; no secret is returned


@router.delete("/accounts/{aid}")
def disconnect(aid: int, request: Request, db: Session = Depends(get_db), user: CurrentUser = Depends(customer_user)):
    acct = _own(db, PublishAccount, aid, user)
    cancelled = cp.disconnect(db, user.id, acct)
    audit(db, request, user, "customer_publish.disconnect", f"account:{aid}", {"platform": acct.platform, "cancelled": cancelled})
    db.commit()
    return {"ok": True, "cancelled": cancelled}


class RuleIn(BaseModel):
    account_id: int
    streamer_id: int
    enabled: bool = True
    require_approval: bool = True
    min_score: float = Field(70.0, ge=0, le=100)
    privacy: str = Field("private", pattern="^(private|unlisted|public)$")
    slots: list[str] = Field(default_factory=lambda: ["12:00", "18:00", "21:00"], max_length=12)
    rights_confirmed: bool = False
    rights_note: str = Field("", max_length=500)


def _apply_rule(db: Session, user: CurrentUser, r: PublishRule, body: RuleIn) -> None:
    acct = _own(db, PublishAccount, body.account_id, user)
    if not db.scalar(select(ChannelMonitor.id).where(ChannelMonitor.user_id == user.id, ChannelMonitor.streamer_id == body.streamer_id,
                                                     ChannelMonitor.removed_at.is_(None))):
        raise HTTPException(404, "canal não encontrado entre os seus canais")
    options = (acct.meta or {}).get("privacy_options") or []
    if options and body.privacy not in options:
        raise HTTPException(422, f"privacidade '{body.privacy}' não existe nesta rede ({', '.join(options)})")
    if any(not cp.SLOT_RE.match(x) for x in body.slots):
        raise HTTPException(422, "horários no formato HH:MM")
    if body.enabled and not body.rights_confirmed and r.rights_confirmed_at is None:
        raise HTTPException(409, "confirme que você tem direito de republicar este canal")
    r.account_id, r.streamer_id, r.enabled = acct.id, body.streamer_id, body.enabled
    r.require_approval, r.min_score, r.privacy = body.require_approval, body.min_score, body.privacy
    r.slots, r.mode, r.timezone = sorted(set(body.slots)), "scheduled", (cp.settings_for(db, user.id, create=True).timezone)
    r.allow_public_auto = body.privacy == "public"  # the customer chose public explicitly for its own destination
    r.max_per_day = cp.plan_limits(entitlements.current(db, user.id))["daily"]  # informative; the total is enforced
    if body.rights_confirmed:
        r.rights_confirmed_at, r.rights_note = utcnow(), body.rights_note.strip()


@router.post("/rules", status_code=201)
def create_rule(body: RuleIn, request: Request, db: Session = Depends(get_db), user: CurrentUser = Depends(customer_user)):
    _require_plan(db, user)
    r = PublishRule(owner_id=user.id, account_id=body.account_id)
    _apply_rule(db, user, r, body)
    db.add(r)
    db.flush()
    audit(db, request, user, "customer_publish.rule_create", f"rule:{r.id}",
          {"account": r.account_id, "streamer": r.streamer_id, "rights": body.rights_confirmed})
    db.commit()
    return _rule(db, r)


@router.put("/rules/{rid}")
def update_rule(rid: int, body: RuleIn, request: Request, db: Session = Depends(get_db),
                user: CurrentUser = Depends(customer_user)):
    _require_plan(db, user)
    r = _own(db, PublishRule, rid, user)
    _apply_rule(db, user, r, body)
    audit(db, request, user, "customer_publish.rule_update", f"rule:{rid}", {"enabled": r.enabled})
    db.commit()
    return _rule(db, r)


@router.delete("/rules/{rid}")
def delete_rule(rid: int, request: Request, db: Session = Depends(get_db), user: CurrentUser = Depends(customer_user)):
    r = _own(db, PublishRule, rid, user)
    for p in db.scalars(select(Publication).where(Publication.rule_id == rid, Publication.owner_id == user.id)):
        cp.cancel(db, p, "regra removida")
    db.delete(r)
    audit(db, request, user, "customer_publish.rule_delete", f"rule:{rid}")
    db.commit()
    return {"ok": True}


@router.post("/publications/{pid}/approve")
def approve(pid: int, request: Request, db: Session = Depends(get_db), user: CurrentUser = Depends(customer_user)):
    _require_plan(db, user)
    p = _own(db, Publication, pid, user)
    if p.status != "proposed":
        raise HTTPException(409, "esta publicação não está aguardando aprovação")
    p.status = "waiting"
    service.log_event(p, "approved", "aprovada pelo cliente")
    cp.plan_waiting(db, user.id)
    audit(db, request, user, "customer_publish.approve", f"publication:{pid}")
    db.commit()
    return _pub(db, p)


@router.post("/publications/{pid}/cancel")
def cancel(pid: int, request: Request, db: Session = Depends(get_db), user: CurrentUser = Depends(customer_user)):
    p = _own(db, Publication, pid, user)
    if p.status not in ("proposed", "waiting", "scheduled"):
        raise HTTPException(409, "esta publicação já começou ou terminou")
    cp.cancel(db, p, "cancelada pelo cliente")
    audit(db, request, user, "customer_publish.cancel", f"publication:{pid}")
    db.commit()
    return _pub(db, p)
