"""Auto publisher API (Phase 4). Tokens and client secrets are never returned."""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import get_db, utcnow
from ..models import Clip, Publication, PublishAccount, PublishApp, PublishRule, Streamer
from ..publish import service
from ..publish.service import PLATFORM_NAME, PublishRefused
from .deps import CurrentUser, audit, current_user, require_admin

router = APIRouter(prefix="/api/publish", tags=["publish"])
PLATFORMS = ("youtube", "tiktok", "instagram")


def _iso(dt):
    return dt.isoformat() + "Z" if dt else None


def _refused(exc: PublishRefused) -> HTTPException:
    return HTTPException(422, {"message": "não é possível publicar", "problems": exc.problems})


def _account(a: PublishAccount) -> dict:
    return {"id": a.id, "platform": a.platform, "platform_name": PLATFORM_NAME.get(a.platform, a.platform),
            "external_id": a.external_id, "name": a.name, "handle": a.handle, "status": a.status, "scopes": a.scopes,
            "meta": a.meta, "quota": a.quota, "last_error": a.last_error, "connected_by": a.connected_by,
            "connected_at": _iso(a.connected_at), "token_expires_at": _iso(a.token_expires_at)}


def _rule(r: PublishRule) -> dict:
    return {k: getattr(r, k) for k in ("id", "account_id", "streamer_id", "enabled", "mode", "require_approval",
                                         "min_score", "privacy", "allow_public_auto", "max_per_day", "slots",
                                         "timezone", "templates")}


def _pub(db: Session, p: Publication) -> dict:
    clip = db.get(Clip, p.clip_id) if p.clip_id else None
    return {"id": p.id, "clip_id": p.clip_id, "clip_title": clip.title if clip else None,
            "clip_score": clip.score if clip else None, "account_id": p.account_id, "rule_id": p.rule_id,
            "platform": p.platform, "status": p.status, "origin": p.origin, "scheduled_at": _iso(p.scheduled_at),
            "title": p.title, "description": p.description, "tags": p.tags, "privacy": p.privacy,
            "remote_id": p.remote_id, "remote_url": p.remote_url, "remote_status": p.remote_status,
            "metrics": p.metrics, "metrics_at": _iso(p.metrics_at), "attempts": p.attempts, "last_error": p.last_error,
            "log": p.log, "created_by": p.created_by, "published_at": _iso(p.published_at),
            "created_at": _iso(p.created_at), "upload_progress": {k: v for k, v in (p.upload_state or {}).items()
                                                                  if k in ("size", "sent", "started")}}


@router.get("/overview")
def overview(db: Session = Depends(get_db), _: CurrentUser = Depends(current_user)):
    apps = {}
    for pf in PLATFORMS:
        row = db.get(PublishApp, pf)
        apps[pf] = {"platform": pf, "name": PLATFORM_NAME[pf], "configured": bool(row and row.client_id and row.client_secret_enc),
                    "client_id": (row.client_id[:12] + "…") if row and row.client_id else "",
                    "has_secret": bool(row and row.client_secret_enc), "redirect_uri": row.redirect_uri if row else "",
                    "validated": pf == "youtube"}
    accounts = [_account(a) for a in db.scalars(select(PublishAccount).order_by(PublishAccount.platform))]
    rules = [_rule(r) for r in db.scalars(select(PublishRule).order_by(PublishRule.id))]
    counts: dict[str, int] = {}
    for (st,) in db.execute(select(Publication.status)):
        counts[st] = counts.get(st, 0) + 1
    return {"apps": apps, "accounts": accounts, "rules": rules, "counts": counts}


class AppIn(BaseModel):
    client_id: str = Field("", max_length=512)  # empty = keep stored client id
    client_secret: str = Field("", max_length=1024)  # empty = keep stored secret
    redirect_uri: str = Field(..., max_length=1024)

    @field_validator("redirect_uri")
    @classmethod
    def _uri(cls, v: str) -> str:
        if not re.match(r"^https?://[^\s]+$", v.strip()):
            raise ValueError("redirect_uri must be an http(s) URL registered in the platform console")
        return v.strip()


@router.put("/apps/{platform}")
def put_app(platform: str, body: AppIn, request: Request, db: Session = Depends(get_db),
            user: CurrentUser = Depends(require_admin)):
    if platform not in PLATFORMS:
        raise HTTPException(404, "unknown platform")
    try:
        service.save_app(db, platform, body.client_id, body.client_secret or None, body.redirect_uri, user.email)
    except PublishRefused as exc:
        raise _refused(exc) from exc
    audit(db, request, user, "publish.app", platform, {"redirect_uri": body.redirect_uri,
                                                       "secret_changed": bool(body.client_secret)})
    db.commit()
    return {"ok": True}


@router.post("/oauth/{platform}/start")
def oauth_start(platform: str, request: Request, db: Session = Depends(get_db), user: CurrentUser = Depends(require_admin)):
    try:
        out = service.start_oauth(db, platform, user.email)
    except PublishRefused as exc:
        raise _refused(exc) from exc
    audit(db, request, user, "publish.oauth_start", platform)
    db.commit()
    return out


class FinishIn(BaseModel):
    redirected_url: str = Field(..., max_length=4096)


@router.post("/oauth/{platform}/finish")
def oauth_finish(platform: str, body: FinishIn, request: Request, db: Session = Depends(get_db),
                 user: CurrentUser = Depends(require_admin)):
    try:
        code, state = service.parse_redirect(body.redirected_url)
        acct = service.finish_oauth(db, platform, code, state, user.email)
    except PublishRefused as exc:
        db.commit()  # the consumed state stays consumed
        raise _refused(exc) from exc
    audit(db, request, user, "publish.connect", f"account:{acct.id}", {"platform": platform, "name": acct.name})
    db.commit()
    return _account(acct)


@router.get("/oauth/{platform}/callback")
def oauth_callback(platform: str, request: Request, db: Session = Depends(get_db),
                   user: CurrentUser = Depends(require_admin)):
    """When the registered redirect URI points to this API (e.g. via https://localhost tunnel)."""
    try:
        code, state = service.parse_redirect(str(request.url))
        acct = service.finish_oauth(db, platform, code, state, user.email)
    except PublishRefused as exc:
        db.commit()
        return RedirectResponse(f"/publicacao?erro={'; '.join(exc.problems)[:300]}")
    audit(db, request, user, "publish.connect", f"account:{acct.id}", {"platform": platform, "name": acct.name})
    db.commit()
    return RedirectResponse("/publicacao?conectado=1")


@router.delete("/accounts/{aid}")
def disconnect(aid: int, request: Request, db: Session = Depends(get_db), user: CurrentUser = Depends(require_admin)):
    a = db.get(PublishAccount, aid)
    if a is None:
        raise HTTPException(404, "account not found")
    a.tokens_enc, a.status = "", "revoked"
    for p in db.scalars(select(Publication).where(Publication.account_id == aid, Publication.status == "scheduled")):
        p.status, p.last_error = "cancelled", "conta desconectada"
    audit(db, request, user, "publish.disconnect", f"account:{aid}", {"platform": a.platform, "name": a.name})
    db.commit()
    return {"ok": True}


class RuleIn(BaseModel):
    account_id: int
    streamer_id: int | None = None
    enabled: bool = True
    mode: str = Field("manual", pattern="^(manual|scheduled|immediate)$")
    require_approval: bool = True
    min_score: float = Field(70.0, ge=0, le=100)
    privacy: str = Field("private", pattern="^(private|unlisted|public|SELF_ONLY|MUTUAL_FOLLOW_FRIENDS|FOLLOWER_OF_CREATOR|PUBLIC_TO_EVERYONE)$")
    allow_public_auto: bool = False
    max_per_day: int = Field(3, ge=1, le=50)
    slots: list[str] = Field(default_factory=list, max_length=24)
    timezone: str = "America/Sao_Paulo"
    templates: dict = Field(default_factory=dict)

    @field_validator("slots")
    @classmethod
    def _slots(cls, v):
        for s in v:
            if not re.match(r"^([01]\d|2[0-3]):[0-5]\d$", s):
                raise ValueError(f"invalid slot {s!r} (HH:MM)")
        return sorted(set(v))


def _check_rule(db: Session, body: RuleIn) -> PublishAccount:
    acct = db.get(PublishAccount, body.account_id)
    if acct is None:
        raise HTTPException(404, "account not found")
    if body.mode != "manual" and acct.platform == "tiktok":
        raise HTTPException(422, "TikTok exige consentimento do criador a cada postagem: use o modo manual")
    if body.mode != "manual" and acct.platform == "instagram" and not body.allow_public_auto:
        raise HTTPException(422, "Reels são públicos: postagem automática no Instagram exige ativar 'permitir público automático'")
    if body.streamer_id is not None and db.get(Streamer, body.streamer_id) is None:
        raise HTTPException(404, "streamer not found")
    return acct


@router.post("/rules")
def create_rule(body: RuleIn, request: Request, db: Session = Depends(get_db), user: CurrentUser = Depends(require_admin)):
    _check_rule(db, body)
    r = PublishRule(**body.model_dump())
    db.add(r)
    db.flush()
    audit(db, request, user, "publish.rule_create", f"rule:{r.id}", body.model_dump())
    db.commit()
    return _rule(r)


@router.put("/rules/{rid}")
def update_rule(rid: int, body: RuleIn, request: Request, db: Session = Depends(get_db),
                user: CurrentUser = Depends(require_admin)):
    r = db.get(PublishRule, rid)
    if r is None:
        raise HTTPException(404, "rule not found")
    _check_rule(db, body)
    for k, v in body.model_dump().items():
        setattr(r, k, v)
    audit(db, request, user, "publish.rule_update", f"rule:{rid}", body.model_dump())
    db.commit()
    return _rule(r)


@router.delete("/rules/{rid}")
def delete_rule(rid: int, request: Request, db: Session = Depends(get_db), user: CurrentUser = Depends(require_admin)):
    r = db.get(PublishRule, rid)
    if r is None:
        raise HTTPException(404, "rule not found")
    db.delete(r)
    audit(db, request, user, "publish.rule_delete", f"rule:{rid}")
    db.commit()
    return {"ok": True}


@router.get("/clips/{cid}/check")
def check_clip(cid: int, account_id: int, privacy: str = "private", db: Session = Depends(get_db),
               _: CurrentUser = Depends(current_user)):
    clip, acct = db.get(Clip, cid), db.get(PublishAccount, account_id)
    if clip is None or acct is None:
        raise HTTPException(404, "clip or account not found")
    streamer = db.get(Streamer, clip.streamer_id)
    return {"problems": service.eligibility(db, clip, acct, auto=False, privacy=privacy, rights_confirmed=True),
            "rights_on_file": bool(((streamer.preferences or {}) if streamer else {}).get("republish_allowed")),
            "suggested": service.suggest_metadata(db, clip, acct.platform),
            "privacy_options": (acct.meta or {}).get("privacy_options") or []}


class PublishIn(BaseModel):
    clip_id: int
    account_id: int
    title: str = Field(..., min_length=1, max_length=150)
    description: str = Field("", max_length=4900)
    tags: list[str] = Field(default_factory=list, max_length=30)
    privacy: str = "private"
    scheduled_at: datetime | None = None
    rights_confirmed: bool = False


@router.post("/publications")
def create_publication(body: PublishIn, request: Request, db: Session = Depends(get_db),
                       user: CurrentUser = Depends(require_admin)):
    clip, acct = db.get(Clip, body.clip_id), db.get(PublishAccount, body.account_id)
    if clip is None or acct is None:
        raise HTTPException(404, "clip or account not found")
    when = None
    if body.scheduled_at is not None:
        when = body.scheduled_at.astimezone(timezone.utc).replace(tzinfo=None) if body.scheduled_at.tzinfo else body.scheduled_at
        if when < utcnow() - timedelta(minutes=1):
            raise HTTPException(422, "o horário agendado já passou")
    try:
        pub = service.create_publication(db, clip, acct, origin="manual", user=user.email, scheduled_at=when,
                                         title=body.title, description=body.description,
                                         tags=[t.lstrip("#")[:60] for t in body.tags], privacy=body.privacy,
                                         rights_confirmed=body.rights_confirmed)
    except PublishRefused as exc:
        db.rollback()
        raise _refused(exc) from exc
    audit(db, request, user, "publish.create", f"publication:{pub.id}",
          {"clip": clip.id, "account": acct.id, "privacy": body.privacy, "rights_confirmed": body.rights_confirmed})
    service.enqueue_due(db)
    db.commit()
    return _pub(db, pub)


@router.get("/publications")
def list_publications(status: str | None = None, days: int = 30, db: Session = Depends(get_db),
                      _: CurrentUser = Depends(current_user)):
    q = select(Publication).where(Publication.created_at >= utcnow() - timedelta(days=max(1, min(days, 365))))
    if status:
        q = q.where(Publication.status.in_(status.split(",")))
    rows = db.scalars(q.order_by(Publication.scheduled_at.desc().nullsfirst(), Publication.id.desc()).limit(500))
    return [_pub(db, p) for p in rows]


@router.post("/publications/{pid}/cancel")
def cancel(pid: int, request: Request, db: Session = Depends(get_db), user: CurrentUser = Depends(require_admin)):
    p = db.get(Publication, pid)
    if p is None:
        raise HTTPException(404, "publication not found")
    if p.status not in ("draft", "scheduled"):
        raise HTTPException(409, "só é possível cancelar antes do envio começar")
    p.status = "cancelled"
    service.log_event(p, "cancelled", f"por {user.email}")
    audit(db, request, user, "publish.cancel", f"publication:{pid}")
    db.commit()
    return _pub(db, p)


@router.post("/publications/{pid}/retry")
def retry(pid: int, request: Request, db: Session = Depends(get_db), user: CurrentUser = Depends(require_admin)):
    """Administrative resend of a failed publication (resumes the saved upload session if any)."""
    p = db.get(Publication, pid)
    if p is None:
        raise HTTPException(404, "publication not found")
    if p.status != "failed":
        raise HTTPException(409, "só publicações com falha podem ser reenviadas")
    if p.remote_id and p.platform != "tiktok":
        raise HTTPException(409, "esta publicação já tem um vídeo na plataforma; verifique-o lá antes de reenviar")
    p.status, p.scheduled_at, p.attempts, p.last_error = "scheduled", None, 0, ""
    service.log_event(p, "retry_admin", f"por {user.email}")
    audit(db, request, user, "publish.retry", f"publication:{pid}")
    service.enqueue_due(db)
    db.commit()
    return _pub(db, p)


@router.post("/publications/{pid}/metrics")
def refresh_metrics(pid: int, db: Session = Depends(get_db), _: CurrentUser = Depends(require_admin)):
    p = db.get(Publication, pid)
    if p is None or p.status != "published":
        raise HTTPException(409, "métricas só existem para publicações concluídas")
    from ..publish.runner import refresh_metrics as do

    db.close()
    do([pid])
    from ..db import session_scope

    with session_scope() as s2:
        return _pub(s2, s2.get(Publication, pid))
