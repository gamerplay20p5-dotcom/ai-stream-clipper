from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..db import get_db, utcnow
from ..models import User
from ..security import create_user_token, verify_password
from .deps import COOKIE, CurrentUser, audit, check_login_rate, client_ip, current_user, jwt_secret

router = APIRouter(prefix="/api/auth", tags=["auth"])


class LoginIn(BaseModel):
    email: str = Field(max_length=255)
    password: str = Field(max_length=256)


@router.post("/login")
def login(body: LoginIn, request: Request, response: Response, db: Session = Depends(get_db)):
    check_login_rate(client_ip(request))
    user = db.scalar(select(User).where(User.email == body.email.strip().lower(), User.active.is_(True)))
    if user is None or not verify_password(user.password_hash, body.password):
        audit(db, request, None, "auth.login_failed", body.email[:255])
        db.commit()
        raise HTTPException(401, "invalid credentials")
    s = get_settings()
    token = create_user_token(jwt_secret(), user.email, user.role, s.jwt_ttl_minutes)
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
def me(user: CurrentUser = Depends(current_user)):
    return {"email": user.email, "role": user.role}


@router.get("/config")
def auth_config():
    s = get_settings()
    return {"oidc_enabled": bool(s.oidc_issuer and s.oidc_client_id), "oidc_issuer": s.oidc_issuer or None}
