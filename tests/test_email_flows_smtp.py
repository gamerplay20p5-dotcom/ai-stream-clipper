"""E-mail flows through a REAL SMTP conversation (STARTTLS with certificate verification, login, delivery).

* API: self sign-up -> confirmation e-mail -> link creates the password -> customer signs in; forgotten password ->
  reset e-mail -> link replaces the password (the old one stops working); unknown e-mails get nothing.
* Commercial service: InfinitePay renewal link e-mailed by nodemailer (commercial/integration/renewal-mail.mjs).

The SMTP server is tests/smtp_sink.py on 127.0.0.1 with a self-signed certificate that the clients are told to trust
(SSL_CERT_FILE for Python, NODE_EXTRA_CA_CERTS for Node). Nothing leaves the machine.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from conftest import CSRF
from smtp_sink import SmtpSink, make_certificate
from test_billing_entitlements import billing_env  # noqa: F401  (billing link configured: sign-up needs it)

ROOT = Path(__file__).resolve().parents[1]
LINK = re.compile(r"https?://\S+/definir-senha\?t=([A-Za-z0-9_\-]+)")


@pytest.fixture
def sink(tmp_path, monkeypatch):
    cert, key = make_certificate(tmp_path)
    s = SmtpSink(cert, key).start()
    monkeypatch.setenv("SSL_CERT_FILE", str(cert))  # the API verifies the server certificate (default context)
    yield s
    s.stop()


@pytest.fixture
def smtp_on(admin, sink, monkeypatch):
    from clipper.config import reset_settings_cache

    monkeypatch.setenv("ASC_PUBLIC_ORIGIN", "https://clipper.example")
    reset_settings_cache()
    r = admin.put("/api/admin/smtp", json={"host": "localhost", "port": sink.port, "username": sink.username,
                                            "password": sink.password, "from_addr": "AI Stream Clipper <no-reply@mg.example>",
                                            "security": "starttls"}, headers=CSRF)
    assert r.status_code == 200 and r.json()["configured"] is True, r.text
    yield sink
    admin.put("/api/admin/smtp", json={"host": "", "from_addr": "", "password": ""}, headers=CSRF)
    reset_settings_cache()


def _token(msg) -> str:
    match = LINK.search(msg.text)
    assert match, msg.text
    return match.group(1)


def test_admin_smtp_test_message_goes_over_tls_with_login(admin, smtp_on):
    assert admin.post("/api/admin/smtp/test", json={"to": "operador@example.com"}, headers=CSRF).json() == {"ok": True}
    [m] = smtp_on.messages
    assert (m.tls, m.user, m.recipients) == (True, smtp_on.username, ["operador@example.com"])


def test_signup_confirms_the_email_and_creates_the_password(app, smtp_on):
    anon = TestClient(app)
    assert anon.get("/api/auth/config").json()["signup_enabled"] is True
    email = f"novo-{uuid.uuid4().hex[:8]}@example.com"
    assert anon.post("/api/auth/signup", json={"email": email}).status_code == 202
    [m] = [x for x in smtp_on.messages if email in x.recipients]
    assert m.tls and "Confirme seu e-mail" in m.message["Subject"]
    # Unusable until the owner of the e-mail opens the link
    assert anon.post("/api/auth/login", json={"email": email, "password": "senha-nova-forte-1"}).status_code == 401
    token = _token(m)
    assert anon.post("/api/auth/password-link", json={"token": token, "new_password": "senha-nova-forte-1"}).status_code == 200
    # Single use
    assert anon.post("/api/auth/password-link", json={"token": token, "new_password": "outra-senha-forte-2"}).status_code != 200
    c = TestClient(app)
    me = c.post("/api/auth/login", json={"email": email, "password": "senha-nova-forte-1"})
    assert me.status_code == 200 and me.json()["role"] == "customer"


def test_forgotten_password_resets_and_unknown_emails_get_nothing(app, smtp_on):
    anon = TestClient(app)
    email = f"esqueci-{uuid.uuid4().hex[:8]}@example.com"
    anon.post("/api/auth/signup", json={"email": email})
    anon.post("/api/auth/password-link", json={"token": _token(smtp_on.messages[-1]), "new_password": "senha-antiga-forte-1"})
    before = len(smtp_on.messages)
    assert anon.post("/api/auth/forgot", json={"email": "ninguem-" + email}).json() == {"email_enabled": True}
    assert len(smtp_on.messages) == before, "no e-mail for an address without an account"
    assert anon.post("/api/auth/forgot", json={"email": email}).json() == {"email_enabled": True}
    m = smtp_on.messages[-1]
    assert email in m.recipients and "Redefinir a senha" in m.message["Subject"]
    assert anon.post("/api/auth/password-link", json={"token": _token(m), "new_password": "senha-trocada-forte-2"}).status_code == 200
    assert TestClient(app).post("/api/auth/login", json={"email": email, "password": "senha-antiga-forte-1"}).status_code == 401
    assert TestClient(app).post("/api/auth/login", json={"email": email, "password": "senha-trocada-forte-2"}).status_code == 200


@pytest.mark.skipif(shutil.which("node") is None or not (ROOT / "commercial" / "node_modules").is_dir(),
                    reason="needs Node.js and commercial/node_modules")
def test_infinitepay_renewal_link_is_emailed_by_the_commercial_service(sink):
    env = {**os.environ, "SMTP_HOST": "localhost", "SMTP_PORT": str(sink.port), "SMTP_SECURE": "false",
           "SMTP_USER": sink.username, "SMTP_PASS": sink.password, "SMTP_FROM": "AI Stream Clipper <no-reply@mg.example>",
           "NODE_EXTRA_CA_CERTS": str(sink.cert), "RECIPIENT": "assinante@example.com"}
    out = subprocess.run([shutil.which("node"), "integration/renewal-mail.mjs"], cwd=ROOT / "commercial", env=env,
                         capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    result = json.loads(out.stdout.strip().splitlines()[-1])
    assert result["firstPaid"] is True
    renewal = result["renewal"]
    assert renewal and renewal["status"] == "open" and renewal["emailed_at"], result
    [m] = sink.messages
    assert (m.tls, m.user, m.recipients) == (True, sink.username, ["assinante@example.com"])
    assert "Renove seu plano" in m.message["Subject"]
    assert renewal["link_url"] in m.text, "the renewal e-mail carries the exact InfinitePay link"
    assert m.message["X-Mailgun-Track"] == "no", "links are never rewritten by provider tracking"


@pytest.fixture
def signup_closed(monkeypatch):
    """Production default: e-mail configured, billing linked, but ASC_SIGNUP_OPEN not set."""
    from clipper.config import reset_settings_cache

    monkeypatch.setenv("ASC_SIGNUP_OPEN", "false")
    monkeypatch.setenv("ASC_SIGNUP_ALLOWED_EMAILS", "Controlada@Example.com, outra@example.com")
    reset_settings_cache()
    yield
    reset_settings_cache()


def test_configuring_smtp_does_not_open_public_signup(app, smtp_on, signup_closed):
    from sqlalchemy import select

    from clipper.db import session_scope
    from clipper.models import User

    anon = TestClient(app)
    config = anon.get("/api/auth/config").json()
    assert config["email_enabled"] is True and config["signup_enabled"] is False
    before = len(smtp_on.messages)
    email = f"publico-{uuid.uuid4().hex[:8]}@example.com"
    r = anon.post("/api/auth/signup", json={"email": email})
    assert r.status_code == 503 and r.json()["detail"] == "cadastro indisponível no momento"
    assert len(smtp_on.messages) == before, "no e-mail while sign-up is closed"
    with session_scope() as db:
        assert db.scalar(select(User.id).where(User.email == email)) is None
    # Password recovery keeps working for existing accounts
    assert anon.post("/api/auth/forgot", json={"email": "ninguem@example.com"}).json() == {"email_enabled": True}


def test_controlled_account_signs_up_while_public_signup_is_closed(app, smtp_on, signup_closed):
    anon = TestClient(app)
    assert anon.post("/api/auth/signup", json={"email": " Controlada@EXAMPLE.com "}).status_code == 202
    assert anon.get("/api/auth/config").json()["signup_enabled"] is False
    [m] = [x for x in smtp_on.messages if "controlada@example.com" in x.recipients]
    assert "Confirme seu e-mail" in m.message["Subject"]
    assert anon.post("/api/auth/password-link", json={"token": _token(m), "new_password": "acesso-homologado-forte-7"}).status_code == 200
    me = TestClient(app).post("/api/auth/login", json={"email": "controlada@example.com", "password": "acesso-homologado-forte-7"})
    assert me.status_code == 200 and me.json()["role"] == "customer"
    # Recovery for the controlled account
    anon.post("/api/auth/forgot", json={"email": "controlada@example.com"})
    reset = smtp_on.messages[-1]
    assert "Redefinir a senha" in reset.message["Subject"] and "controlada@example.com" in reset.recipients
