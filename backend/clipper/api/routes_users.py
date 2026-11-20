"""Administration of accounts and shares (multi-user stage 1). Staff (admin) only.

No public sign-up, no e-mail, no SMTP: the admin creates an account and hands the initial
password over personally. New accounts are external viewers; admin only when chosen explicitly.
"""

from __future__ import annotations

import secrets
from datetime import datetime, timezone
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .. import accounts, sharing
from ..db import get_db
from ..models import Clip, ClipShare, Streamer, StreamerGrant, User
from ..security import hash_password
from .deps import CurrentUser, audit, require_admin
from .routes_auth import public_origin

router = APIRouter(tags=["users"])
EMAIL_RE = r"^[^@\s]{1,64}@[^@\s]{1,190}\.[^@\s]{2,}$"


def _iso(dt):
    return dt.isoformat() + "Z" if dt else None


def _naive_utc(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    return dt.astimezone(timezone.utc).replace(tzinfo=None) if dt.tzinfo else dt


def _user(db: Session, u: User) -> dict:
    n = db.scalar(select(func.count()).select_from(ClipShare).where(ClipShare.user_id == u.id,
                                                                    ClipShare.revoked_at.is_(None))) or 0
    return {"id": u.id, "email": u.email, "role": u.role, "active": u.active, "active_shares": n,
            "created_at": _iso(u.created_at), "last_login_at": _iso(u.last_login_at)}


def _share(db: Session, s: ClipShare) -> dict:
    c = db.get(Clip, s.clip_id)
    u = db.get(User, s.user_id)
    current = bool(c and (s.follow_latest or s.video_path == c.video_path))
    return {"id": s.id, "clip_id": s.clip_id, "clip_title": c.title if c else None, "user_id": s.user_id,
            "follow_latest": s.follow_latest, "shared_version": s.clip_version, "grant_id": s.grant_id,
            "outdated": not current,  # the clip was re-edited after this share: the old version is still served
            "email": u.email if u else None, "can_view": s.can_view, "can_download": s.can_download,
            "can_variants": s.can_variants, "expires_at": _iso(s.expires_at), "created_at": _iso(s.created_at),
            "revoked_at": _iso(s.revoked_at),
            "state": "revogado" if s.revoked_at else (
                "expirado" if s.expires_at and s.expires_at <= datetime.utcnow() else "ativo")}


def _initial_password() -> str:
    return secrets.token_urlsafe(15)  # ~120 bits; shown ONCE to the admin, only its hash is stored


def _admins_left(db: Session, excluding: int) -> int:
    return db.scalar(select(func.count()).select_from(User).where(
        User.role == "admin", User.active.is_(True), User.id != excluding)) or 0


# ------------------------------------------------------------------ accounts
class UserIn(BaseModel):
    email: str = Field(..., max_length=255, pattern=EMAIL_RE)
    role: Literal["viewer", "admin"] = "viewer"
    # how the person gets access: a link to create the password (recommended), the link by e-mail, or an
    # initial password shown once to the admin (previous behaviour)
    delivery: Literal["password", "link", "email"] = "password"


class UserPatch(BaseModel):
    active: bool | None = None
    role: Literal["viewer", "admin"] | None = None


@router.get("/api/admin/users")
def list_users(db: Session = Depends(get_db), _: CurrentUser = Depends(require_admin)):
    return [_user(db, u) for u in db.scalars(select(User).order_by(User.role, User.email))]


@router.post("/api/admin/users")
def create_user(body: UserIn, request: Request, db: Session = Depends(get_db),
                admin: CurrentUser = Depends(require_admin)):
    email = sharing.normalize_email(body.email)
    if db.scalar(select(User).where(User.email == email)):
        raise HTTPException(409, "já existe uma conta com este e-mail")
    if body.delivery == "email" and not accounts.smtp_ready(db):
        raise HTTPException(409, "o envio de e-mail não está configurado: use o link ou a senha inicial")
    pw = _initial_password()
    u = User(email=email, password_hash=hash_password(pw), role=body.role, active=True, session_version=0)
    db.add(u)
    db.flush()
    audit(db, request, admin, "users.create", f"user:{u.id}", {"role": u.role, "delivery": body.delivery})
    if body.delivery == "password":
        db.commit()
        return {**_user(db, u), "initial_password": pw,
                "note": "Entregue esta senha pessoalmente. Ela não é guardada nem enviada por e-mail."}
    u.password_hash = ""  # nobody knows a password until the person creates one with the link
    raw = accounts.issue_link(db, u, "invite", admin.email)
    url = f"{public_origin(request)}/definir-senha?t={raw}"
    db.commit()
    sent = _maybe_send(db, u.email, url, "invite", body.delivery == "email")
    return {**_user(db, u), "setup_url": url, "emailed": sent}


def _maybe_send(db: Session, to: str, url: str, purpose: str, wanted: bool) -> bool:
    if not wanted:
        return False
    subject, text = accounts.link_mail(url, purpose)
    try:
        accounts.send_mail(db, to, subject, text)
    except Exception as exc:
        raise HTTPException(502, f"o link foi criado, mas o e-mail não saiu: {type(exc).__name__}: {exc}"[:300]) from exc
    return True


class LinkIn(BaseModel):
    send_email: bool = False


@router.post("/api/admin/users/{uid}/password-link")
def password_link(uid: int, body: LinkIn, request: Request, db: Session = Depends(get_db),
                  admin: CurrentUser = Depends(require_admin)):
    """Single-use link for the person to create a new password (the current one keeps working until then)."""
    u = db.get(User, uid)
    if u is None:
        raise HTTPException(404, "conta não encontrada")
    if not u.active:
        raise HTTPException(409, "a conta está desativada: reative antes de gerar o link")
    if body.send_email and not accounts.smtp_ready(db):
        raise HTTPException(409, "o envio de e-mail não está configurado")
    purpose = "invite" if not u.password_hash else "reset"
    raw = accounts.issue_link(db, u, purpose, admin.email)
    url = f"{public_origin(request)}/definir-senha?t={raw}"
    audit(db, request, admin, "users.password_link", f"user:{uid}", {"purpose": purpose, "email": body.send_email})
    db.commit()
    sent = _maybe_send(db, u.email, url, purpose, body.send_email)
    return {"id": u.id, "email": u.email, "setup_url": url, "emailed": sent, "purpose": purpose,
            "expires_hours": 72 if purpose == "invite" else 2}


class SmtpIn(BaseModel):
    host: str = Field("", max_length=255)
    port: int = Field(587, ge=1, le=65535)
    username: str = Field("", max_length=255)
    password: str | None = Field(None, max_length=512)  # None keeps the stored one, "" removes it
    from_addr: str = Field("", max_length=255)
    security: Literal["starttls", "ssl", "none"] = "starttls"


@router.get("/api/admin/smtp")
def get_smtp(db: Session = Depends(get_db), _: CurrentUser = Depends(require_admin)):
    return accounts.smtp_public(db)  # the password never leaves the server


@router.put("/api/admin/smtp")
def put_smtp(body: SmtpIn, request: Request, db: Session = Depends(get_db), admin: CurrentUser = Depends(require_admin)):
    accounts.save_smtp(db, host=body.host, port=body.port, username=body.username, password=body.password,
                       from_addr=body.from_addr, security=body.security)
    audit(db, request, admin, "smtp.update", "smtp", {"host": body.host, "password_changed": body.password is not None})
    db.commit()
    return accounts.smtp_public(db)


class SmtpTestIn(BaseModel):
    to: str = Field(..., max_length=255, pattern=EMAIL_RE)


@router.post("/api/admin/smtp/test")
def test_smtp(body: SmtpTestIn, db: Session = Depends(get_db), _: CurrentUser = Depends(require_admin)):
    try:
        accounts.send_mail(db, body.to, "Teste de e-mail / E-mail test (AI Stream Clipper)",
                           "Se você recebeu esta mensagem, o envio de e-mail do AI Stream Clipper está funcionando.\n"
                           "If you got this message, AI Stream Clipper e-mail sending works.\n")
    except Exception as exc:
        raise HTTPException(502, f"falhou: {type(exc).__name__}: {exc}"[:300]) from exc
    return {"ok": True}


@router.patch("/api/admin/users/{uid}")
def update_user(uid: int, body: UserPatch, request: Request, db: Session = Depends(get_db),
                admin: CurrentUser = Depends(require_admin)):
    u = db.get(User, uid)
    if u is None:
        raise HTTPException(404, "conta não encontrada")
    losing_admin = (body.role == "viewer" and u.role == "admin") or (body.active is False and u.role == "admin")
    if losing_admin and _admins_left(db, u.id) == 0:
        raise HTTPException(409, "esta é a última conta de administrador ativa")
    if uid == admin.id and (body.active is False or body.role == "viewer"):
        raise HTTPException(409, "você não pode desativar nem rebaixar a própria conta")
    changed = {}
    if body.active is not None and body.active != u.active:
        u.active, changed["active"] = body.active, body.active
    if body.role is not None and body.role != u.role:
        u.role, changed["role"] = body.role, body.role
    if changed:
        u.session_version = (u.session_version or 0) + 1  # open sessions end on the next request
        audit(db, request, admin, "users.update", f"user:{uid}", changed)
    db.commit()
    return _user(db, u)


@router.post("/api/admin/users/{uid}/reset-password")
def reset_password(uid: int, request: Request, db: Session = Depends(get_db),
                   admin: CurrentUser = Depends(require_admin)):
    u = db.get(User, uid)
    if u is None:
        raise HTTPException(404, "conta não encontrada")
    pw = _initial_password()
    u.password_hash = hash_password(pw)
    u.session_version = (u.session_version or 0) + 1
    audit(db, request, admin, "users.reset_password", f"user:{uid}")
    db.commit()
    return {"id": u.id, "email": u.email, "initial_password": pw,
            "note": "Entregue esta senha pessoalmente. As sessões abertas desta conta foram encerradas."}


@router.post("/api/admin/users/{uid}/end-sessions")
def end_sessions(uid: int, request: Request, db: Session = Depends(get_db),
                 admin: CurrentUser = Depends(require_admin)):
    u = db.get(User, uid)
    if u is None:
        raise HTTPException(404, "conta não encontrada")
    u.session_version = (u.session_version or 0) + 1
    audit(db, request, admin, "users.end_sessions", f"user:{uid}")
    db.commit()
    return {"ok": True}


@router.get("/api/admin/users/{uid}/shares")
def user_shares(uid: int, include_revoked: bool = False, db: Session = Depends(get_db),
                _: CurrentUser = Depends(require_admin)):
    q = select(ClipShare).where(ClipShare.user_id == uid)
    if not include_revoked:
        q = q.where(ClipShare.revoked_at.is_(None))
    return [_share(db, s) for s in db.scalars(q.order_by(ClipShare.id.desc()))]


# ------------------------------------------------------------------ shares
class ShareIn(BaseModel):
    email: str = Field(..., max_length=255)
    clip_ids: list[int] = Field(..., min_length=1, max_length=200)
    can_download: bool = False
    can_variants: bool = False
    expires_at: datetime | None = None
    follow_latest: bool = False  # default: the exact version approved now, never silently replaced

    @field_validator("email")
    @classmethod
    def _email(cls, v: str) -> str:
        return sharing.normalize_email(v)


class SharePatch(BaseModel):
    can_download: bool | None = None
    can_variants: bool | None = None
    expires_at: datetime | None = None
    clear_expiry: bool = False


@router.post("/api/shares")
def create_shares(body: ShareIn, request: Request, db: Session = Depends(get_db),
                  admin: CurrentUser = Depends(require_admin)):
    try:
        out = sharing.share(db, admin_id=admin.id, email=body.email, clip_ids=body.clip_ids,
                            can_download=body.can_download, can_variants=body.can_variants,
                            expires_at=_naive_utc(body.expires_at), follow_latest=body.follow_latest)
    except sharing.ShareError as exc:
        db.rollback()
        raise HTTPException(404 if "não encontrada" in str(exc) else 422, str(exc)) from exc
    audit(db, request, admin, "shares.create", f"user:{out['user_id']}",
          {"clips": body.clip_ids, "download": body.can_download, "variants": body.can_variants,
           "expires_at": _iso(_naive_utc(body.expires_at))})
    db.commit()
    return out


@router.get("/api/shares")
def list_shares(clip_id: int | None = None, user_id: int | None = None, include_revoked: bool = False,
                db: Session = Depends(get_db), _: CurrentUser = Depends(require_admin)):
    q = select(ClipShare)
    if clip_id is not None:
        q = q.where(ClipShare.clip_id == clip_id)
    if user_id is not None:
        q = q.where(ClipShare.user_id == user_id)
    if not include_revoked:
        q = q.where(ClipShare.revoked_at.is_(None))
    return [_share(db, s) for s in db.scalars(q.order_by(ClipShare.id.desc()).limit(1000))]


@router.patch("/api/shares/{sid}")
def update_share(sid: int, body: SharePatch, request: Request, db: Session = Depends(get_db),
                 admin: CurrentUser = Depends(require_admin)):
    s = db.get(ClipShare, sid)
    if s is None or s.revoked_at is not None:
        raise HTTPException(404, "compartilhamento ativo não encontrado")
    data = body.model_dump(exclude_unset=True)
    if "can_download" in data:
        s.can_download = bool(data["can_download"])
    if "can_variants" in data:
        s.can_variants = bool(data["can_variants"])
    if body.clear_expiry:
        s.expires_at = None
    elif body.expires_at is not None:
        s.expires_at = _naive_utc(body.expires_at)
    audit(db, request, admin, "shares.update", f"share:{sid}", {k: str(v) for k, v in data.items()})
    db.commit()
    return _share(db, s)


@router.post("/api/shares/{sid}/reapprove")
def reapprove_share(sid: int, request: Request, db: Session = Depends(get_db),
                    admin: CurrentUser = Depends(require_admin)):
    """Explicitly switch an active share to the clip's CURRENT version (a re-edit never does it alone)."""
    s = db.get(ClipShare, sid)
    if s is None or s.revoked_at is not None:
        raise HTTPException(404, "compartilhamento ativo não encontrado")
    c = db.get(Clip, s.clip_id)
    if c is None or not c.video_path:
        raise HTTPException(409, "o corte não tem vídeo renderizado")
    sharing.snapshot(s, c)
    audit(db, request, admin, "shares.reapprove", f"share:{sid}", {"version": c.version})
    db.commit()
    return _share(db, s)


@router.delete("/api/shares/{sid}")
def revoke_share(sid: int, request: Request, db: Session = Depends(get_db), admin: CurrentUser = Depends(require_admin)):
    """Revoke (kept as history). Effective on the next request of that account."""
    try:
        s = sharing.revoke(db, sid, admin.id)
    except sharing.ShareError as exc:
        raise HTTPException(404, str(exc)) from exc
    audit(db, request, admin, "shares.revoke", f"share:{sid}")
    db.commit()
    return _share(db, s)


# ------------------------------------------------------------------ access by streamer
def _grant(db: Session, g: StreamerGrant) -> dict:
    st = db.get(Streamer, g.streamer_id)
    u = db.get(User, g.user_id)
    n = db.scalar(select(func.count()).select_from(ClipShare).where(ClipShare.grant_id == g.id,
                                                                    ClipShare.revoked_at.is_(None))) or 0
    return {"id": g.id, "user_id": g.user_id, "email": u.email if u else None, "streamer_id": g.streamer_id,
            "streamer": (st.display_name or st.channel) if st else None, "can_download": g.can_download,
            "include_pending": bool(g.include_pending),
            "expires_at": _iso(g.expires_at), "created_at": _iso(g.created_at), "revoked_at": _iso(g.revoked_at),
            "active_shares": n,
            "state": "revogado" if g.revoked_at else (
                "expirado" if g.expires_at and g.expires_at <= datetime.utcnow() else "ativo")}


class GrantIn(BaseModel):
    email: str = Field(..., max_length=255)
    streamer_id: int
    can_download: bool = False
    include_pending: bool = False  # also the clips still waiting for review
    expires_at: datetime | None = None


@router.post("/api/streamer-grants")
def create_grant(body: GrantIn, request: Request, db: Session = Depends(get_db),
                 admin: CurrentUser = Depends(require_admin)):
    """The account sees every APPROVED clip of the streamer: the current ones and every later approval."""
    try:
        g, n = sharing.grant_streamer(db, admin_id=admin.id, email=body.email, streamer_id=body.streamer_id,
                                      can_download=body.can_download, expires_at=_naive_utc(body.expires_at),
                                      include_pending=body.include_pending)
    except sharing.ShareError as exc:
        db.rollback()
        raise HTTPException(404 if "não encontrad" in str(exc) else 422, str(exc)) from exc
    audit(db, request, admin, "grants.create", f"user:{g.user_id}",
          {"streamer": body.streamer_id, "download": body.can_download, "pending": body.include_pending,
           "shared_now": n})
    db.commit()
    return {**_grant(db, g), "shared_now": n}


@router.get("/api/streamer-grants")
def list_grants(user_id: int | None = None, include_revoked: bool = False, db: Session = Depends(get_db),
                _: CurrentUser = Depends(require_admin)):
    q = select(StreamerGrant)
    if user_id is not None:
        q = q.where(StreamerGrant.user_id == user_id)
    if not include_revoked:
        q = q.where(StreamerGrant.revoked_at.is_(None))
    return [_grant(db, g) for g in db.scalars(q.order_by(StreamerGrant.id.desc()))]


@router.delete("/api/streamer-grants/{gid}")
def revoke_grant(gid: int, request: Request, db: Session = Depends(get_db), admin: CurrentUser = Depends(require_admin)):
    """Revoke the access and every share it created (explicit shares of the same clips are kept)."""
    try:
        g, n = sharing.revoke_grant(db, gid, admin.id)
    except sharing.ShareError as exc:
        raise HTTPException(404, str(exc)) from exc
    audit(db, request, admin, "grants.revoke", f"grant:{gid}", {"shares_revoked": n})
    db.commit()
    return {**_grant(db, g), "shares_revoked": n}
