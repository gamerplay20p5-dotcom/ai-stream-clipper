"""Passwords, user tokens and internal request signing."""

from __future__ import annotations

import hashlib
import hmac
import secrets
import time
from dataclasses import dataclass

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError

_ph = PasswordHasher()


def hash_password(password: str) -> str:
    return _ph.hash(password)


def verify_password(password_hash: str, password: str) -> bool:
    if not password_hash:
        return False
    try:
        return _ph.verify(password_hash, password)
    except (VerifyMismatchError, InvalidHashError):
        return False


def create_user_token(secret: str, email: str, role: str, ttl_minutes: int) -> str:
    now = int(time.time())
    return jwt.encode(
        {"sub": email, "role": role, "iat": now, "exp": now + ttl_minutes * 60, "jti": secrets.token_hex(8)},
        secret,
        algorithm="HS256",
    )


def decode_user_token(secret: str, token: str) -> dict | None:
    try:
        return jwt.decode(token, secret, algorithms=["HS256"])
    except jwt.PyJWTError:
        return None


# ---------------------------------------------------------------------------
# Internal HMAC signing (API <-> workers <-> segment servers).
# The shared secret never travels on the wire; each request carries a timestamped
# signature over method, path+query and the SHA-256 of the body.
# ---------------------------------------------------------------------------

HDR_WORKER = "X-ASC-Worker"
HDR_TS = "X-ASC-Timestamp"
HDR_SIG = "X-ASC-Signature"
HDR_BODY_SHA = "X-ASC-Content-SHA256"
EMPTY_SHA = hashlib.sha256(b"").hexdigest()


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path, chunk: int = 1 << 20) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            b = f.read(chunk)
            if not b:
                break
            h.update(b)
    return h.hexdigest()


def _canonical(method: str, path_qs: str, ts: str, body_sha: str, worker: str) -> bytes:
    return "\n".join([method.upper(), path_qs, ts, body_sha, worker]).encode()


def sign_request(secret: str, worker: str, method: str, path_qs: str, body_sha: str) -> dict[str, str]:
    ts = str(int(time.time()))
    sig = hmac.new(secret.encode(), _canonical(method, path_qs, ts, body_sha, worker), hashlib.sha256).hexdigest()
    return {HDR_WORKER: worker, HDR_TS: ts, HDR_SIG: sig, HDR_BODY_SHA: body_sha}


@dataclass
class SignatureCheck:
    ok: bool
    worker: str = ""
    reason: str = ""


def verify_signature(
    secret: str,
    headers,
    method: str,
    path_qs: str,
    body_sha: str | None,
    max_skew: int = 60,
) -> SignatureCheck:
    """Verify an internal request. ``body_sha`` is the server-side hash of the body, or
    ``None`` for streaming uploads where the declared hash is checked after storage."""
    if not secret:
        return SignatureCheck(False, reason="internal secret not configured")
    worker = headers.get(HDR_WORKER, "")
    ts = headers.get(HDR_TS, "")
    sig = headers.get(HDR_SIG, "")
    declared = headers.get(HDR_BODY_SHA, "")
    if not (worker and ts and sig and declared):
        return SignatureCheck(False, reason="missing signature headers")
    try:
        ts_i = int(ts)
    except ValueError:
        return SignatureCheck(False, reason="bad timestamp")
    if abs(time.time() - ts_i) > max_skew:
        return SignatureCheck(False, reason="timestamp skew too large")
    if body_sha is not None and not hmac.compare_digest(body_sha, declared):
        return SignatureCheck(False, reason="body hash mismatch")
    expected = hmac.new(secret.encode(), _canonical(method, path_qs, ts, declared, worker), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, sig):
        return SignatureCheck(False, reason="bad signature")
    return SignatureCheck(True, worker=worker)
