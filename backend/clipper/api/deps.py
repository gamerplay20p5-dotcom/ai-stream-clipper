"""Shared FastAPI dependencies: user auth (cookie/JWT + CSRF), worker auth (HMAC), audit."""

from __future__ import annotations

import logging
import secrets
import time
from collections import defaultdict, deque
from dataclasses import dataclass

from fastapi import Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..db import get_db
from ..models import AuditLog, User
from ..security import decode_user_token, sha256_bytes, verify_signature

log = logging.getLogger(__name__)

COOKIE = "asc_session"
CSRF_HEADER = "x-asc-csrf"
SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}

_ephemeral_secret = secrets.token_urlsafe(48)


def jwt_secret() -> str:
    s = get_settings()
    if s.jwt_secret:
        return s.jwt_secret
    if s.is_production:
        raise RuntimeError("ASC_JWT_SECRET is required in production")
    return _ephemeral_secret


@dataclass
class CurrentUser:
    id: int
    email: str
    role: str

    @property
    def is_admin(self) -> bool:
        return self.role == "admin"


def current_user(request: Request, db: Session = Depends(get_db)) -> CurrentUser:
    token = request.cookies.get(COOKIE)
    via_cookie = token is not None
    if not token:
        auth = request.headers.get("authorization", "")
        if auth.lower().startswith("bearer "):
            token = auth[7:].strip()
    if not token:
        raise HTTPException(401, "not authenticated")
    claims = decode_user_token(jwt_secret(), token)
    if not claims:
        raise HTTPException(401, "session expired")
    if via_cookie and request.method not in SAFE_METHODS and request.headers.get(CSRF_HEADER) != "1":
        raise HTTPException(403, "missing CSRF header")
    user = db.scalar(select(User).where(User.email == claims.get("sub"), User.active.is_(True)))
    if user is None:
        raise HTTPException(401, "unknown user")
    return CurrentUser(user.id, user.email, user.role)


def require_admin(user: CurrentUser = Depends(current_user)) -> CurrentUser:
    if not user.is_admin:
        raise HTTPException(403, "admin role required")
    return user


def client_ip(request: Request) -> str:
    return request.client.host if request.client else ""


def audit(db: Session, request: Request, user: CurrentUser | None, action: str, target: str = "",
          details: dict | None = None) -> None:
    db.add(AuditLog(user_email=user.email if user else "", action=action, target=target[:255],
                    details=details or {}, ip=client_ip(request)))


# --- login throttling ------------------------------------------------------

_attempts: dict[str, deque] = defaultdict(deque)


def check_login_rate(ip: str, limit: int = 10, window: float = 300.0) -> None:
    """Block an IP after ``limit`` FAILED logins within ``window`` seconds."""
    now = time.monotonic()
    q = _attempts[ip]
    while q and now - q[0] > window:
        q.popleft()
    if len(q) >= limit:
        raise HTTPException(429, "too many login attempts, try again later")


def record_login_failure(ip: str) -> None:
    _attempts[ip].append(time.monotonic())


# --- worker auth -------------------------------------------------------------


def _path_qs(request: Request) -> str:
    qs = request.url.query
    return request.url.path + (f"?{qs}" if qs else "")


async def worker_auth(request: Request) -> str:
    """HMAC-verified internal request (body hash checked)."""
    s = get_settings()
    body = await request.body()
    chk = verify_signature(s.internal_secret, request.headers, request.method, _path_qs(request),
                           sha256_bytes(body), s.signature_max_skew_s)
    if not chk.ok:
        log.warning("internal auth rejected", extra={"reason": chk.reason, "path": request.url.path})
        raise HTTPException(401, f"internal auth failed: {chk.reason}")
    return chk.worker


async def worker_auth_upload(request: Request) -> tuple[str, str]:
    """HMAC check for streaming uploads; returns ``(worker, declared_sha)`` - the handler
    must compare the declared hash with the hash of the stored body."""
    s = get_settings()
    chk = verify_signature(s.internal_secret, request.headers, request.method, _path_qs(request), None,
                           s.signature_max_skew_s)
    if not chk.ok:
        raise HTTPException(401, f"internal auth failed: {chk.reason}")
    return chk.worker, request.headers.get("x-asc-content-sha256", "")
