"""Instagram API with Unified Login (Meta config_id) + OAuth callback authenticated by the state."""

from __future__ import annotations

from urllib.parse import parse_qs, urlsplit

import httpx
import pytest

from clipper.db import session_scope
from clipper.models import PublishAccount
from clipper.publish import instagram
from clipper.publish.base import App, PublishError, Tokens
from conftest import CSRF

CALLBACK = "https://192.168.1.210:8780/api/publish/oauth/instagram/callback"


class FakeMeta:
    def __init__(self):
        self.exchanges = []

    def handler(self, req: httpx.Request) -> httpx.Response:
        url = str(req.url)
        if "/oauth/access_token" in url and "graph.facebook.com/v27.0" in url:
            q = dict(req.url.params)
            self.exchanges.append(q)
            assert q["client_id"] == "1234567890" and q["client_secret"] == "meta-secret" and q["code"] == "AQB"
            return httpx.Response(200, json={"access_token": "SUAT-token", "token_type": "bearer"})
        if url.startswith("https://graph.facebook.com/v27.0/me/assigned_instagram_accounts"):
            return httpx.Response(200, json={"data": [{"id": "17841400000000001", "username": "cortes_teste"},
                                                      {"id": "17841400000000002", "username": "segunda_conta"}]})
        return httpx.Response(404, json={"error": url})


@pytest.fixture()
def fake_meta(monkeypatch):
    fm = FakeMeta()
    monkeypatch.setattr(instagram, "_client", lambda: httpx.Client(transport=httpx.MockTransport(fm.handler)))
    return fm


def test_unified_auth_url_uses_config_id_without_scope():
    app = App("instagram", "1234567890", "s", CALLBACK, config_id="987654321")
    u = instagram.auth_url(app, "st", "ch")
    parts = urlsplit(u)
    q = parse_qs(parts.query)
    assert parts.netloc == "www.facebook.com" and parts.path == "/dialog/oauth"
    assert q["config_id"] == ["987654321"] and q["redirect_uri"] == [CALLBACK] and q["response_type"] == ["code"]
    assert "scope" not in q and q["state"] == ["st"]
    legacy = parse_qs(urlsplit(instagram.auth_url(App("instagram", "1", "s", CALLBACK), "st", "ch")).query)
    assert "scope" in legacy  # without config_id the Instagram Login flow is unchanged


def test_unified_tokens_cannot_refresh_silently():
    with pytest.raises(PublishError) as exc:
        instagram.refresh(App("instagram", "1", "s", CALLBACK, "9"), Tokens("x", extra={"flow": "unified"}))
    assert exc.value.auth and not exc.value.retryable
    assert instagram._base(Tokens("x", extra={"flow": "unified"})).startswith("https://graph.facebook.com/v27.0")


def test_callback_without_cookie_connects_every_shared_account(admin, app, fake_meta):
    r = admin.put("/api/publish/apps/instagram", json={"client_id": "1234567890", "client_secret": "meta-secret",
                                                       "redirect_uri": CALLBACK, "config_id": "987654321"},
                  headers=CSRF)
    assert r.status_code == 200, r.text
    ov = admin.get("/api/publish/overview").json()["apps"]["instagram"]
    assert ov["config_id"] == "987654321" and ov["callback_url"].startswith("https://clipper.howto.software/") and "meta-secret" not in str(ov)
    assert admin.put("/api/publish/apps/instagram", json={"redirect_uri": CALLBACK, "config_id": "abc"},
                     headers=CSRF).status_code == 422
    start = admin.post("/api/publish/oauth/instagram/start", headers=CSRF).json()
    assert "config_id=987654321" in start["auth_url"]
    # the redirect from facebook.com arrives WITHOUT the SameSite=strict panel cookie
    from fastapi.testclient import TestClient

    anon = TestClient(app, follow_redirects=False)  # no session cookie
    r = anon.get(f"/api/publish/oauth/instagram/callback?code=AQB&state={start['state']}")
    assert r.status_code == 303 and r.headers["location"].endswith("/publicacao?conectado=1")
    with session_scope() as db:
        accts = db.query(PublishAccount).filter(PublishAccount.platform == "instagram").all()
        ids = {a.external_id for a in accts}
        assert {"17841400000000001", "17841400000000002"} <= ids
        assert all(a.connected_by == "admin@asc.local" or "@" in a.connected_by for a in accts)
        assert all("SUAT-token" not in a.tokens_enc for a in accts)
    # replay of the same state (or a forged one) is refused and lands on the panel with the reason
    again = anon.get(f"/api/publish/oauth/instagram/callback?code=AQB&state={start['state']}")
    assert again.status_code == 303 and "erro=" in again.headers["location"]
    assert len(fake_meta.exchanges) == 1


def test_instagram_without_config_id_never_opens_the_legacy_dialog(admin):
    admin.put("/api/publish/apps/instagram", json={"client_id": "1234567890", "client_secret": "meta-secret",
                                                   "redirect_uri": CALLBACK, "config_id": ""}, headers=CSRF)
    r = admin.post("/api/publish/oauth/instagram/start", headers=CSRF)
    assert r.status_code == 422 and "config_id" in str(r.json()["detail"])
    assert admin.get("/api/publish/overview").json()["apps"]["instagram"]["flow"] == "missing_config_id"
    admin.put("/api/publish/apps/instagram", json={"redirect_uri": CALLBACK, "config_id": "987654321"}, headers=CSRF)
    ok = admin.post("/api/publish/oauth/instagram/start", headers=CSRF).json()
    assert ok["auth_url"].startswith("https://www.facebook.com/dialog/oauth?") and "instagram.com" not in ok["auth_url"]


def test_wrong_app_can_be_removed(admin):
    admin.put("/api/publish/apps/tiktok", json={"client_id": "1234567890", "client_secret": "x",
                                                "redirect_uri": "https://localhost/asc-callback"}, headers=CSRF)
    assert admin.get("/api/publish/overview").json()["apps"]["tiktok"]["configured"]
    with session_scope() as db:  # with a connected account the app cannot be removed
        db.add(PublishAccount(platform="tiktok", external_id="tt-guard", status="connected"))
    assert admin.delete("/api/publish/apps/tiktok", headers=CSRF).status_code == 422
    with session_scope() as db:
        for a in db.query(PublishAccount).filter(PublishAccount.platform == "tiktok"):
            a.status = "revoked"
    assert admin.delete("/api/publish/apps/tiktok", headers=CSRF).status_code == 200
    assert not admin.get("/api/publish/overview").json()["apps"]["tiktok"]["configured"]


def test_public_callback_returns_the_browser_to_the_lan_panel(admin, app, fake_meta):
    admin.put("/api/publish/apps/instagram", json={"client_id": "1234567890", "client_secret": "meta-secret",
                                                   "redirect_uri": "https://clipper.howto.software/api/publish/oauth/instagram/callback",
                                                   "config_id": "987654321"}, headers=CSRF)
    start = admin.post("/api/publish/oauth/instagram/start", headers=CSRF).json()
    from fastapi.testclient import TestClient

    public = TestClient(app, base_url="https://clipper.howto.software", follow_redirects=False)
    r = public.get(f"/api/publish/oauth/instagram/callback?code=AQB&state={start['state']}")
    assert r.status_code == 303
    assert r.headers["location"] == "http://testserver/publicacao?conectado=1"  # origin that started the flow
    assert admin.get("/api/publish/overview").json()["apps"]["instagram"]["callback_url"] == \
        "https://clipper.howto.software/api/publish/oauth/instagram/callback"
