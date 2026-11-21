"""v0.7.7: people create and change their own password; single-use links; e-mail (SMTP) stored encrypted;
the database host appears in the infrastructure page."""

from __future__ import annotations

import json
from datetime import timedelta
from urllib.parse import parse_qs, urlsplit

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from clipper import accounts
from clipper.api import deps
from clipper.db import session_scope, utcnow
from clipper.models import AppSetting, PasswordToken, User
from conftest import CSRF


@pytest.fixture(autouse=True)
def _no_rate_limit():
    deps._attempts.clear()
    yield
    deps._attempts.clear()


def _new(app) -> TestClient:
    return TestClient(app, headers=CSRF)


def _token(url: str) -> str:
    return parse_qs(urlsplit(url).query)["t"][0]


def test_password_policy():
    assert accounts.password_problems("curta")
    assert accounts.password_problems("aaaaaaaaaaaa")
    assert accounts.password_problems("maria.silva-2026", "maria.silva@x.com")
    assert accounts.password_problems("cavalo-azul-bateria-9", "ana@x.com") == []


def test_invite_link_creates_the_password_once(app, admin):
    r = admin.post("/api/admin/users", json={"email": "convite@cliente.com", "delivery": "link"}, headers=CSRF).json()
    assert "initial_password" not in r and "/definir-senha?t=" in r["setup_url"]
    raw = _token(r["setup_url"])
    with session_scope() as db:
        u = db.scalar(select(User).where(User.email == "convite@cliente.com"))
        assert u.password_hash == ""  # nobody knows a password before the person creates it
        stored = db.scalar(select(PasswordToken).where(PasswordToken.user_id == u.id))
        assert stored.token_hash != raw and len(stored.token_hash) == 64  # only the SHA-256 is stored
    c = _new(app)
    info = c.get(f"/api/auth/password-link?token={raw}").json()
    assert info["purpose"] == "invite" and info["email"].startswith("co") and "•" in info["email"]
    weak = c.post("/api/auth/password-link", json={"token": raw, "new_password": "123"})
    assert weak.status_code == 422 and weak.json()["detail"]["problems"]
    ok = c.post("/api/auth/password-link", json={"token": raw, "new_password": "pedra-lua-cavalo-7"})
    assert ok.status_code == 200
    assert c.post("/api/auth/password-link", json={"token": raw, "new_password": "outra-senha-boa-8"}).status_code == 410
    assert c.post("/api/auth/login", json={"email": "convite@cliente.com", "password": "pedra-lua-cavalo-7"}).status_code == 200


def test_reset_link_replaces_older_links_and_expires(app, admin):
    uid = admin.post("/api/admin/users", json={"email": "reset@cliente.com"}, headers=CSRF).json()["id"]
    first = _token(admin.post(f"/api/admin/users/{uid}/password-link", json={}, headers=CSRF).json()["setup_url"])
    second = _token(admin.post(f"/api/admin/users/{uid}/password-link", json={}, headers=CSRF).json()["setup_url"])
    c = _new(app)
    assert c.get(f"/api/auth/password-link?token={first}").status_code == 410  # replaced
    with session_scope() as db:
        t = db.scalar(select(PasswordToken).where(PasswordToken.user_id == uid, PasswordToken.used_at.is_(None)))
        t.expires_at = utcnow() - timedelta(minutes=1)
    assert c.get(f"/api/auth/password-link?token={second}").status_code == 410  # expired


def test_people_change_their_own_password(app, admin):
    pw = admin.post("/api/admin/users", json={"email": "troca@cliente.com"}, headers=CSRF).json()["initial_password"]
    c, other = _new(app), _new(app)
    for x in (c, other):
        assert x.post("/api/auth/login", json={"email": "troca@cliente.com", "password": pw}).status_code == 200
    assert c.post("/api/auth/password", json={"current_password": "errada-123456", "new_password": "nova-senha-forte-1"}).status_code == 403
    assert c.post("/api/auth/password", json={"current_password": pw, "new_password": "curta"}).status_code == 422
    assert c.post("/api/auth/password", json={"current_password": pw, "new_password": "nova-senha-forte-1"}).status_code == 200
    assert c.get("/api/auth/me").status_code == 200  # this session continues
    assert other.get("/api/auth/me").status_code == 401  # the other sessions end
    assert _new(app).post("/api/auth/login", json={"email": "troca@cliente.com", "password": pw}).status_code == 401


def test_forgot_password_needs_email_and_never_reveals_accounts(app, admin, monkeypatch):
    c = _new(app)
    assert c.post("/api/auth/forgot", json={"email": "ninguem@x.com"}).json() == {"email_enabled": False}
    sent = []
    monkeypatch.setattr(accounts, "send_mail", lambda db, to, subject, body: sent.append((to, body)))
    r = admin.put("/api/admin/smtp", json={"host": "smtp.exemplo.com", "port": 587, "username": "bot",
                                            "password": "segredo-smtp-123", "from_addr": "clipper@exemplo.com"},
                  headers=CSRF).json()
    assert r["has_password"] and r["configured"] and "password" not in r and "password_enc" not in r
    with session_scope() as db:
        stored = json.dumps(db.get(AppSetting, "smtp").value)
        assert "segredo-smtp-123" not in stored  # encrypted at rest
        assert accounts.crypto.decrypt(db.get(AppSetting, "smtp").value["password_enc"]) == "segredo-smtp-123"
    assert c.get("/api/auth/config").json()["email_enabled"] is True
    uid = admin.post("/api/admin/users", json={"email": "esqueci@cliente.com"}, headers=CSRF).json()["id"]
    assert c.post("/api/auth/forgot", json={"email": "ninguem@x.com"}).json() == {"email_enabled": True}
    assert c.post("/api/auth/forgot", json={"email": "esqueci@cliente.com"}).json() == {"email_enabled": True}
    assert len(sent) == 1 and sent[0][0] == "esqueci@cliente.com" and "/definir-senha?t=" in sent[0][1]
    admin.put("/api/admin/smtp", json={"host": "", "from_addr": "", "password": ""}, headers=CSRF)  # back off
    with session_scope() as db:
        assert db.scalar(select(PasswordToken).where(PasswordToken.user_id == uid, PasswordToken.purpose == "reset"))


def test_database_host_is_listed(admin):
    hosts = {h["label"]: h for h in admin.get("/api/infrastructure").json()["hosts"]}
    assert hosts["212"]["ip"] == "192.168.1.212" and "Banco de dados" in hosts["212"]["role"]


def test_migration_0015_up_and_down(tmp_path):
    import sqlalchemy as sa

    from clipper.migrate import downgrade_db, upgrade_db

    url = f"sqlite:///{(tmp_path / 'p.db').as_posix()}"
    upgrade_db(url)
    eng = sa.create_engine(url)
    assert "password_tokens" in sa.inspect(eng).get_table_names()
    downgrade_db(url, "0014")
    assert "password_tokens" not in sa.inspect(sa.create_engine(url)).get_table_names()
    upgrade_db(url)
