"""Signed calls from the API to the commercial service (checkout and Stripe portal for an account).

Same HMAC canonical format as the workers (``security.sign_request``) with the dedicated ``billing_secret``;
the commercial service accepts only caller ``main``. The account id sent here is what links a payment to the
account: the commercial service never binds a payment to an account by a typed e-mail.
"""

from __future__ import annotations

import json

import httpx

from .config import get_settings
from .security import sha256_bytes, sign_request

CALLER = "main"


class BillingUnavailable(RuntimeError):
    pass


def configured() -> bool:
    s = get_settings()
    return len(s.billing_secret or "") >= 32 and bool(s.commercial_url)


def call(path: str, body: dict, *, transport: httpx.BaseTransport | None = None) -> tuple[int, dict]:
    s = get_settings()
    if not configured():
        raise BillingUnavailable("billing link not configured")
    raw = json.dumps(body, separators=(",", ":")).encode()
    headers = sign_request(s.billing_secret, CALLER, "POST", path, sha256_bytes(raw))
    try:
        with httpx.Client(base_url=s.commercial_url.rstrip("/"), timeout=15.0, transport=transport) as client:
            r = client.post(path, content=raw, headers={**headers, "Content-Type": "application/json"})
    except httpx.HTTPError as exc:
        raise BillingUnavailable(type(exc).__name__) from exc
    try:
        data = r.json()
    except ValueError:
        data = {}
    return r.status_code, data if isinstance(data, dict) else {}
