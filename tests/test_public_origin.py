"""E-mailed links use only the operator's ASC_PUBLIC_ORIGIN (host-header poisoning) and SMTP never sends
credentials or links without TLS."""

from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient

from conftest import CSRF

ORIGIN = "https://clipper.example.test"  # tests/conftest.py
POISON = {"Host": "evil.example", "Origin": "https://evil.example", "X-Forwarded-Host": "evil.example",
          "X-Forwarded-Proto": "http", "Forwarded": "host=evil.example;proto=http", "Referer": "https://evil.example/x"}


@pytest.fixture
def mail(client, monkeypatch):  # client: starts the app (database) for the session
    from clipper import accounts

    sent = []
    monkeypatch.setattr(accounts, "smtp_ready", lambda db: True)
    monkeypatch.setattr(accounts, "send_mail", lambda db, to, subject, text: sent.append((to, text)))
    return sent


def _admin(app) -> TestClient:
    c = TestClient(app)
    assert c.post("/api/auth/login", json={"email": "admin@example.com", "password": "correct-horse-battery"}).status_code == 200
    return c


def _links(text: str) -> list[str]:
    return [w for w in text.split() if "definir-senha" in w]


@pytest.mark.parametrize("value,expected", [
    ("https://Clipper.Example.com", "https://clipper.example.com"),
    ("https://clipper.example.com/", "https://clipper.example.com"),
    ("http://localhost:5173", "http://localhost:5173"),
    ("  https://a.example:8443  ", "https://a.example:8443"),
    ("", ""),
])
def test_canonical_origin_normalizes(value, expected):
    from clipper.config import canonical_origin

    assert canonical_origin(value) == expected


@pytest.mark.parametrize("value", [
    "clipper.example.com", "ftp://clipper.example.com", "https://clipper.example.com/painel", "https://x.example?a=1",
    "https://x.example#frag", "https://user:pw@x.example", "https:///nohost", "https://x.example:99999",
    "javascript:alert(1)", "https://ex ample.com", "https://exaтmple.com",
])
def test_invalid_origin_configuration_is_rejected(value, monkeypatch):
    from pydantic import ValidationError

    from clipper.config import Settings

    monkeypatch.setenv("ASC_PUBLIC_ORIGIN", value)
    with pytest.raises(ValidationError):
        Settings()


def test_production_requires_an_https_origin(monkeypatch):
    from clipper.config import Settings

    for value, ok in [("", False), ("http://clipper.example.com", False), ("http://192.168.1.210:8780", False),
                      ("https://clipper.example.com", True), ("http://127.0.0.1:8780", True)]:
        monkeypatch.setenv("ASC_PUBLIC_ORIGIN", value)
        s = Settings(env="production")
        if ok:
            s.require_public_origin()
        else:
            with pytest.raises(RuntimeError, match="ASC_PUBLIC_ORIGIN"):
                s.require_public_origin()


def test_forgot_and_signup_links_ignore_forged_host_headers(app, mail, monkeypatch):
    from clipper import billing_link

    monkeypatch.setattr(billing_link, "configured", lambda: True)
    c = TestClient(app)
    assert c.post("/api/auth/forgot", json={"email": "admin@example.com"}, headers=POISON).status_code == 200
    email = f"novo-{uuid.uuid4().hex[:8]}@example.com"
    assert c.post("/api/auth/signup", json={"email": email}, headers=POISON).status_code == 202
    assert len(mail) == 2
    for _, text in mail:
        links = _links(text)
        assert links and all(link.startswith(f"{ORIGIN}/definir-senha?t=") for link in links), text
        assert "evil" not in text


def test_invite_and_reset_links_ignore_forged_host_headers(app, mail):
    admin = _admin(app)
    email = f"convite-{uuid.uuid4().hex[:8]}@example.com"
    r = admin.post("/api/admin/users", json={"email": email, "role": "viewer", "delivery": "email"}, headers={**CSRF, **POISON})
    assert r.status_code == 200, r.text
    assert r.json()["setup_url"].startswith(f"{ORIGIN}/definir-senha?t=")
    uid = r.json()["id"]
    r = admin.post(f"/api/admin/users/{uid}/password-link", json={"send_email": True}, headers={**CSRF, **POISON})
    assert r.status_code == 200, r.text
    assert r.json()["setup_url"].startswith(f"{ORIGIN}/definir-senha?t=")
    assert all("evil" not in text for _, text in mail)
    # The token still works through the canonical link
    token = r.json()["setup_url"].split("t=")[1]
    assert TestClient(app).post("/api/auth/password-link", json={"token": token, "new_password": "senha-bem-longa-42"}).status_code == 200


def test_without_origin_no_link_is_built(app, mail, monkeypatch):
    from clipper.config import reset_settings_cache

    monkeypatch.setenv("ASC_PUBLIC_ORIGIN", "")
    reset_settings_cache()
    try:
        admin = _admin(app)
        r = admin.post("/api/admin/users", json={"email": f"x-{uuid.uuid4().hex[:6]}@example.com", "role": "viewer", "delivery": "link"},
                       headers=CSRF)
        assert r.status_code == 409 and "ASC_PUBLIC_ORIGIN" in r.json()["detail"]
        assert TestClient(app).get("/api/auth/config").json()["signup_enabled"] is False
        assert TestClient(app).post("/api/auth/forgot", json={"email": "admin@example.com"}).json() == {"email_enabled": False}
        assert mail == []
    finally:
        monkeypatch.undo()
        reset_settings_cache()


def test_tokens_never_reach_the_logs(app, mail, caplog):
    import logging

    caplog.set_level(logging.DEBUG)
    TestClient(app).post("/api/auth/forgot", json={"email": "admin@example.com"}, headers=POISON)
    token = _links(mail[-1][1])[0].split("t=")[1]
    assert token not in caplog.text


@pytest.mark.parametrize("cfg,problem", [
    ({"host": "smtp.example.com", "security": "starttls", "username": "u"}, ""),
    ({"host": "smtp.example.com", "security": "ssl", "username": "u"}, ""),
    ({"host": "smtp.example.com", "security": "none", "username": "u"}, "usuário e senha"),
    ({"host": "smtp.example.com", "security": "none", "password_enc": "x"}, "usuário e senha"),
    ({"host": "localhost", "security": "none"}, ""),
    ({"host": "smtp.example.com", "security": "plain"}, "desconhecido"),
])
def test_smtp_without_tls_never_carries_credentials(cfg, problem):
    from clipper import accounts

    found = accounts.smtp_transport_problem(cfg)
    assert (problem in found) if problem else found == ""


def test_smtp_cleartext_relay_only_on_localhost_in_production(monkeypatch):
    from clipper import accounts
    from clipper.config import reset_settings_cache

    monkeypatch.setenv("ASC_ENV", "production")
    reset_settings_cache()
    try:
        assert "relay local" in accounts.smtp_transport_problem({"host": "smtp.example.com", "security": "none"})
        assert accounts.smtp_transport_problem({"host": "127.0.0.1", "security": "none"}) == ""
    finally:
        monkeypatch.undo()
        reset_settings_cache()


def test_admin_cannot_save_or_use_insecure_smtp(app, client, monkeypatch):
    import smtplib

    from clipper import accounts
    from clipper.db import session_scope

    admin = _admin(app)
    r = admin.put("/api/admin/smtp", json={"host": "smtp.example.com", "port": 25, "username": "hts", "password": "segredo",
                                           "from_addr": "noreply@example.com", "security": "none"}, headers=CSRF)
    assert r.status_code == 422 and "TLS" in r.json()["detail"]
    # A configuration stored before this check is refused at send time, before any connection
    monkeypatch.setattr(smtplib, "SMTP", lambda *a, **k: pytest.fail("must not connect"))
    monkeypatch.setattr(accounts, "smtp_config", lambda db: {"host": "smtp.example.com", "from_addr": "a@example.com",
                                                             "security": "none", "username": "hts", "password_enc": "x"})
    with session_scope() as db:
        assert accounts.smtp_ready(db) is False
        with pytest.raises(accounts.InsecureSmtp):
            accounts.send_mail(db, "b@example.com", "s", "t")
