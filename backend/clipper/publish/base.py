"""Common contract of the platform integrations."""

from __future__ import annotations

import base64
import hashlib
import secrets
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from ..db import utcnow


class PublishError(RuntimeError):
    """``retryable`` errors go back to the queue with backoff (``retry_after`` seconds when known)."""

    def __init__(self, message: str, *, retryable: bool = True, retry_after: float | None = None,
                 auth: bool = False):
        super().__init__(message)
        self.retryable = retryable
        self.retry_after = retry_after
        self.auth = auth  # token revoked/expired: account must be reconnected


@dataclass
class App:
    platform: str
    client_id: str
    client_secret: str
    redirect_uri: str


@dataclass
class Tokens:
    access_token: str
    refresh_token: str = ""
    expires_at: datetime | None = None
    scope: str = ""
    extra: dict = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {"access_token": self.access_token, "refresh_token": self.refresh_token,
                "expires_at": self.expires_at.isoformat() if self.expires_at else None, "scope": self.scope,
                "extra": self.extra}

    @staticmethod
    def from_dict(d: dict) -> Tokens:
        exp = datetime.fromisoformat(d["expires_at"]) if d.get("expires_at") else None
        return Tokens(d.get("access_token", ""), d.get("refresh_token", ""), exp, d.get("scope", ""),
                      d.get("extra") or {})

    def expiring(self, margin_s: int = 120) -> bool:
        return self.expires_at is not None and self.expires_at <= utcnow() + timedelta(seconds=margin_s)


@dataclass
class UploadResult:
    remote_id: str
    remote_url: str = ""
    remote_status: dict = field(default_factory=dict)
    state: str = "processing"  # processing | published


def pkce_pair() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(64)[:96]
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    return verifier, challenge


def expires_in(seconds) -> datetime | None:
    try:
        return utcnow() + timedelta(seconds=int(seconds))
    except (TypeError, ValueError):
        return None


def check_response(r, what: str) -> dict:
    """Map an HTTP response to data or a classified PublishError (never leaks tokens)."""
    try:
        data = r.json() if r.content else {}
    except ValueError:
        data = {"raw": r.text[:300]}
    if r.status_code < 400:
        return data
    detail = str(data)[:400]
    retry_after = None
    if r.headers.get("retry-after", "").isdigit():
        retry_after = float(r.headers["retry-after"])
    if r.status_code in (401,):
        raise PublishError(f"{what}: autorização expirada ou revogada ({detail})", retryable=False, auth=True)
    if r.status_code == 429 or "quota" in detail.lower() or "rate" in detail.lower():
        raise PublishError(f"{what}: limite da API atingido ({detail})", retryable=True, retry_after=retry_after or 900)
    if r.status_code >= 500:
        raise PublishError(f"{what}: erro temporário da plataforma HTTP {r.status_code}", retryable=True)
    raise PublishError(f"{what}: HTTP {r.status_code} {detail}", retryable=False)
