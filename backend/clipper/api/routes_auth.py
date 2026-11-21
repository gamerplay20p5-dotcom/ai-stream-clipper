from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..db import get_db, utcnow
from .. import accounts
from ..models import User
from ..security import create_user_token, hash_password, verify_password
from .deps import (COOKIE, CurrentUser, audit, authenticated_user, check_login_rate, client_ip, jwt_secret,
                   record_login_failure)

router = APIRouter(prefix="/api/auth", tags=["auth"])


class LoginIn(BaseModel):
    email: str = Field(max_length=255)
    password: str = Field(max_length=256)


@router.post("/login")
def login(body: LoginIn, request: Request, response: Response, db: Session = Depends(get_db)):
    check_login_rate(client_ip(request))
    user = db.scalar(select(User).where(User.email == body.email.strip().lower(), User.active.is_(True)))
    if user is None or not verify_password(user.password_hash, body.password):
        record_login_failure(client_ip(request))
        audit(db, request, None, "auth.login_failed", body.email[:255])
        db.commit()
        raise HTTPException(401, "invalid credentials")
    s = get_settings()
    token = create_user_token(jwt_secret(), user.email, s.jwt_ttl_minutes, user.session_version or 0, user.id)
    user.last_login_at = utcnow()
    audit(db, request, CurrentUser(user.id, user.email, user.role), "auth.login")
    db.commit()
    response.set_cookie(COOKIE, token, httponly=True, secure=s.cookie_secure, samesite="strict",
                        max_age=s.jwt_ttl_minutes * 60, path="/")
    return {"email": user.email, "role": user.role}


@router.post("/logout")
def logout(response: Response):
    response.delete_cookie(COOKIE, path="/")
    return {"ok": True}


@router.get("/me")
def me(user: CurrentUser = Depends(authenticated_user)):
    return {"email": user.email, "role": user.role, "staff": user.is_admin,
            "areas": ["panel", "library"] if user.is_admin else ["library"]}


@router.get("/config")
def auth_config(db: Session = Depends(get_db)):
    s = get_settings()
    return {"oidc_enabled": bool(s.oidc_issuer and s.oidc_client_id), "oidc_issuer": s.oidc_issuer or None,
            "email_enabled": accounts.smtp_ready(db), "password_min": accounts.MIN_LEN}


def _session_cookie(response: Response, user: User) -> None:
    s = get_settings()
    token = create_user_token(jwt_secret(), user.email, s.jwt_ttl_minutes, user.session_version or 0, user.id)
    response.set_cookie(COOKIE, token, httponly=True, secure=s.cookie_secure, samesite="strict",
                        max_age=s.jwt_ttl_minutes * 60, path="/")


def public_origin(request: Request) -> str:
    """Origin for links: the browser's Origin when it is this same host, else the request base URL."""
    origin = request.headers.get("origin", "")
    host = request.headers.get("host", "")
    if origin and host and origin.split("://", 1)[-1] == host:
        return origin.rstrip("/")
    return str(request.base_url).rstrip("/")


class PasswordChangeIn(BaseModel):
    current_password: str = Field(max_length=256)
    new_password: str = Field(max_length=256)


@router.post("/password")
def change_password(body: PasswordChangeIn, request: Request, response: Response, db: Session = Depends(get_db),
                    who: CurrentUser = Depends(authenticated_user)):
    """The signed-in person changes their own password. Other sessions end; this one continues."""
    check_login_rate(client_ip(request))
    user = db.get(User, who.id)
    if user is None or not verify_password(user.password_hash, body.current_password):
        record_login_failure(client_ip(request))
        audit(db, request, who, "auth.password_change_failed")
        db.commit()
        raise HTTPException(403, "a senha atual não confere")
    problems = accounts.password_problems(body.new_password, user.email)
    if verify_password(user.password_hash, body.new_password):
        problems.append("a nova senha precisa ser diferente da atual")
    if problems:
        raise HTTPException(422, {"message": "senha não aceita", "problems": problems})
    user.password_hash = hash_password(body.new_password)
    user.session_version = (user.session_version or 0) + 1
    audit(db, request, who, "auth.password_changed")  # never the password
    db.commit()
    _session_cookie(response, user)
    return {"ok": True}


@router.get("/password-link")
def check_link(token: str, request: Request, db: Session = Depends(get_db)):
    found = accounts.find_link(db, token)
    if found is None:
        raise HTTPException(410, "este link não vale mais: ele expirou, já foi usado ou foi substituído por outro")
    t, u = found
    return {"email": accounts.mask_email(u.email), "purpose": t.purpose, "expires_at": t.expires_at.isoformat() + "Z",
            "password_min": accounts.MIN_LEN}


class LinkUseIn(BaseModel):
    token: str = Field(max_length=200)
    new_password: str = Field(max_length=256)


@router.post("/password-link")
def use_link(body: LinkUseIn, request: Request, db: Session = Depends(get_db)):
    """Create or reset the password with a single-use link. Every open session of the account ends."""
    check_login_rate(client_ip(request))
    found = accounts.find_link(db, body.token)
    if found is None:
        record_login_failure(client_ip(request))
        raise HTTPException(410, "este link não vale mais: ele expirou, já foi usado ou foi substituído por outro")
    t, u = found
    problems = accounts.password_problems(body.new_password, u.email)
    if problems:
        raise HTTPException(422, {"message": "senha não aceita", "problems": problems})
    u.password_hash = hash_password(body.new_password)
    u.session_version = (u.session_version or 0) + 1
    t.used_at = utcnow()
    audit(db, request, CurrentUser(u.id, u.email, u.role), f"auth.password_link_{t.purpose}")
    db.commit()
    return {"ok": True, "email": u.email}


class ForgotIn(BaseModel):
    email: str = Field(max_length=255)


@router.post("/forgot")
def forgot(body: ForgotIn, request: Request, db: Session = Depends(get_db)):
    """Always the same answer (does not reveal whether the e-mail has an account)."""
    check_login_rate(client_ip(request))
    record_login_failure(client_ip(request))  # counts toward the limit: no e-mail flooding from one IP
    enabled = accounts.smtp_ready(db)
    user = db.scalar(select(User).where(User.email == body.email.strip().lower(), User.active.is_(True)))
    if enabled and user is not None:
        raw = accounts.issue_link(db, user, "reset", "esqueci a senha")
        subject, text = accounts.link_mail(f"{public_origin(request)}/definir-senha?t={raw}", "reset")
        audit(db, request, None, "auth.forgot", f"user:{user.id}")
        db.commit()
        try:
            accounts.send_mail(db, user.email, subject, text)
        except Exception:
            import logging

            logging.getLogger(__name__).exception("forgot-password e-mail failed")
    return {"email_enabled": enabled}
