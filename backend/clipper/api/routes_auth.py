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
    areas = ["panel", "library"] if user.is_admin else ["account", "library"] if user.role == "customer" else ["library"]
    return {"email": user.email, "role": user.role, "staff": user.is_admin, "areas": areas}


@router.get("/config")
def auth_config(db: Session = Depends(get_db)):
    s = get_settings()
    from .. import billing_link

    email = accounts.smtp_ready(db) and accounts.links_ready()
    return {"oidc_enabled": bool(s.oidc_issuer and s.oidc_client_id), "oidc_issuer": s.oidc_issuer or None,
            "email_enabled": email, "password_min": accounts.MIN_LEN,
            # self sign-up needs e-mail (to prove ownership) and the billing link (to subscribe)
            "signup_enabled": email and billing_link.configured()}


def _session_cookie(response: Response, user: User) -> None:
    s = get_settings()
    token = create_user_token(jwt_secret(), user.email, s.jwt_ttl_minutes, user.session_version or 0, user.id)
    response.set_cookie(COOKIE, token, httponly=True, secure=s.cookie_secure, samesite="strict",
                        max_age=s.jwt_ttl_minutes * 60, path="/")


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
    enabled = accounts.smtp_ready(db) and accounts.links_ready()
    user = db.scalar(select(User).where(User.email == body.email.strip().lower(), User.active.is_(True)))
    if enabled and user is not None:
        raw = accounts.issue_link(db, user, "reset", "esqueci a senha")
        subject, text = accounts.link_mail(accounts.password_link_url(raw), "reset")
        audit(db, request, None, "auth.forgot", f"user:{user.id}")
        db.commit()
        try:
            accounts.send_mail(db, user.email, subject, text)
        except Exception:
            import logging

            logging.getLogger(__name__).exception("forgot-password e-mail failed")
    return {"email_enabled": enabled}


class SignupIn(BaseModel):
    email: str = Field(max_length=255)


@router.post("/signup", status_code=202)
def signup(body: SignupIn, request: Request, db: Session = Depends(get_db)):
    """Self sign-up for customers. The account can only be used after the owner of the e-mail opens the
    single-use link and creates a password, so payments are always bound to a verified e-mail.
    Always the same answer: it never reveals whether the e-mail already has an account."""
    from .. import billing_link

    check_login_rate(client_ip(request))
    record_login_failure(client_ip(request))  # counts toward the limit: no e-mail flooding from one IP
    if not (accounts.smtp_ready(db) and accounts.links_ready() and billing_link.configured()):
        raise HTTPException(503, "cadastro indisponível no momento")
    email = body.email.strip().lower()
    if not _EMAIL.fullmatch(email) or len(email) > 254:
        raise HTTPException(422, "e-mail inválido")
    user = db.scalar(select(User).where(User.email == email))
    purpose = ""
    if user is None:
        user = User(email=email, password_hash="", role="customer", active=True)
        db.add(user)
        db.flush()
        purpose = "signup"
    elif user.role == "customer" and user.active:
        purpose = "reset"  # existing customer: a password reset link instead (same response)
    if purpose:
        raw = accounts.issue_link(db, user, purpose, "cadastro")
        subject, text = accounts.link_mail(accounts.password_link_url(raw), purpose)
        audit(db, request, None, f"auth.signup_{purpose}", f"user:{user.id}")
        db.commit()
        try:
            accounts.send_mail(db, user.email, subject, text)
        except Exception:
            import logging

            logging.getLogger(__name__).exception("signup e-mail failed")
    else:
        db.commit()
    return {"ok": True}


import re as _re  # noqa: E402

_EMAIL = _re.compile(r"[a-z0-9.!#$%&'*+/=?^_`{|}~-]+@[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)+")
