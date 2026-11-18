"""Encryption at rest for OAuth tokens and client secrets.

AES-256-GCM with a key derived (HKDF-SHA256, own label) from the deployment's JWT secret,
so no new secret has to be provisioned. Rotating ASC_JWT_SECRET invalidates stored tokens
(accounts must be reconnected) - documented in docs/PUBLICACAO.md.
"""

from __future__ import annotations

import base64
import json
import os
from functools import lru_cache

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from ..config import get_settings

_PREFIX = "v1:"


class CryptoError(RuntimeError):
    pass


@lru_cache(maxsize=4)
def _key(secret: str) -> bytes:
    if not secret or len(secret) < 16:
        raise CryptoError("ASC_JWT_SECRET is required to store publishing credentials")
    return HKDF(algorithm=hashes.SHA256(), length=32, salt=b"asc-publisher", info=b"asc-publish-tokens-v1").derive(
        secret.encode())


def _aes() -> AESGCM:
    return AESGCM(_key(get_settings().jwt_secret))


def encrypt(data: dict | str) -> str:
    raw = json.dumps(data).encode() if not isinstance(data, str) else ("s:" + data).encode()
    nonce = os.urandom(12)
    ct = _aes().encrypt(nonce, raw, b"asc")
    return _PREFIX + base64.urlsafe_b64encode(nonce + ct).decode()


def decrypt(token: str):
    if not token:
        return None
    if not token.startswith(_PREFIX):
        raise CryptoError("unknown ciphertext format")
    blob = base64.urlsafe_b64decode(token[len(_PREFIX):])
    try:
        raw = _aes().decrypt(blob[:12], blob[12:], b"asc")
    except Exception as exc:  # wrong key (secret rotated) or tampering
        raise CryptoError("cannot decrypt stored credential (secret rotated?)") from exc
    text = raw.decode()
    return text[2:] if text.startswith("s:") else json.loads(text)
