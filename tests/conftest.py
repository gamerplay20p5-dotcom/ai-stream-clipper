from __future__ import annotations

import os
import shutil
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests" / "fixtures"
sys.path.insert(0, str(ROOT / "backend"))

_TMP = Path(tempfile.mkdtemp(prefix="asc_tests_"))
os.environ.update({
    "ASC_ENV": "test",
    "ASC_LOG_JSON": "false",
    "ASC_LOG_LEVEL": "WARNING",
    "ASC_DATABASE_URL": f"sqlite:///{(_TMP / 'unit.db').as_posix()}",
    "ASC_STORAGE_DIR": str(_TMP / "storage"),
    "ASC_JWT_SECRET": "test-jwt-secret-0123456789abcdef",
    "ASC_INTERNAL_SECRET": "test-internal-secret-0123456789",
    "ASC_ADMIN_EMAIL": "admin@example.com",
    "ASC_ADMIN_PASSWORD": "correct-horse-battery",
    "ASC_PUBLIC_ORIGIN": "https://clipper.example.test",
    "ASC_ALLOW_FILE_SOURCES": "true",
    "ASC_TEST_MEDIA_DIR": str(FIXTURES),
    "HF_HUB_DISABLE_SYMLINKS_WARNING": "1",
})

from clipper.config import reset_settings_cache  # noqa: E402

reset_settings_cache()

ADMIN = ("admin@example.com", "correct-horse-battery")
CSRF = {"X-ASC-CSRF": "1"}


@pytest.fixture(scope="session")
def tmp_root() -> Path:
    yield _TMP
    shutil.rmtree(_TMP, ignore_errors=True)


@pytest.fixture(scope="session")
def app():
    from clipper.api.app import create_app

    return create_app(run_maintenance=False)


@pytest.fixture(scope="session")
def client(app):
    from fastapi.testclient import TestClient

    with TestClient(app) as c:
        yield c


@pytest.fixture()
def admin(client):
    r = client.post("/api/auth/login", json={"email": ADMIN[0], "password": ADMIN[1]})
    assert r.status_code == 200, r.text
    return client


@pytest.fixture()
def db(client):
    from clipper.db import session_scope

    with session_scope() as s:
        yield s


def sign_headers(method: str, path_qs: str, body: bytes = b"", worker: str = "test-worker") -> dict:
    from clipper.security import sha256_bytes, sign_request

    return sign_request(os.environ["ASC_INTERNAL_SECRET"], worker, method, path_qs, sha256_bytes(body))
