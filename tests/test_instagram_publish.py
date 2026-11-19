"""Instagram Reels publishing end to end against a simulated Meta Graph API (no real post).

Covers the 2026-11-19 bug report: privacy, eligibility reasons, rules/batch validation, Meta error
classification, streamed upload, container status (rejected video), no double media_publish.
"""

from __future__ import annotations

from datetime import timedelta
from types import SimpleNamespace

import httpx
import pytest

from clipper.db import session_scope, utcnow
from clipper.models import Clip, PublishAccount, Streamer, StreamSession
from clipper.publish import crypto, instagram
from clipper.publish.base import PublishError, Tokens
from conftest import CSRF

TOK = Tokens("IGTOKEN", "", utcnow() + timedelta(days=30), "", {"flow": "unified", "user_id": "1789"})


class FakeMeta:
    """Programmable Graph + rupload responses; records what was sent (never a real request)."""

    def __init__(self):
        self.calls: list[tuple[str, str]] = []
        self.container_status = "FINISHED"
        self.publish_count = 0
        self.uploaded = b""
        self.upload_headers: dict = {}
        self.fail: dict[str, httpx.Response] = {}
        self.media_list: list[dict] = []

    def handler(self, req: httpx.Request) -> httpx.Response:
        url = str(req.url)
        self.calls.append((req.method, url.split("?")[0]))
        for key, resp in self.fail.items():
            if key in url:
                return resp
        if "/content_publishing_limit" in url:
            return httpx.Response(200, json={"data": [{"quota_usage": 1, "config": {"quota_total": 50}}]})
        if url.endswith("/1789/media") and req.method == "POST":
            return httpx.Response(200, json={"id": "C123", "uri": "https://rupload.facebook.com/ig-api-upload/v27.0/C123"})
        if url.startswith("https://rupload.facebook.com"):
            self.upload_headers = dict(req.headers)
            self.uploaded = req.read()
            return httpx.Response(200, json={"success": True})
        if "/C123" in url and req.method == "GET":
            return httpx.Response(200, json={"status_code": self.container_status,
                                             "status": "Error: unsupported aspect ratio" if self.container_status == "ERROR" else ""})
        if url.endswith("/media_publish"):
            self.publish_count += 1
            return httpx.Response(200, json={"id": "M999"})
        if "/1789/media" in url and req.method == "GET":
            return httpx.Response(200, json={"data": self.media_list})
        if "/M999" in url:
            return httpx.Response(200, json={"permalink": "https://www.instagram.com/reel/abc/"})
        return httpx.Response(404, json={"error": {"message": "unexpected " + url, "code": 100}})


@pytest.fixture()
def meta(monkeypatch):
    fm = FakeMeta()
    monkeypatch.setattr(instagram, "_client", lambda: httpx.Client(transport=httpx.MockTransport(fm.handler)))
    return fm


def _pub(**kw):
    base = dict(privacy="public", title="A fuga", description="Créditos: X", upload_state={})
    base.update(kw)
    return SimpleNamespace(**base)


def _video(tmp_path, size=3 * 1024 * 1024 + 17):
    p = tmp_path / "v.mp4"
    p.write_bytes(bytes(i % 251 for i in range(size)))
    return p


# ------------------------------------------------------------------ provider
def test_success_flow_container_streamed_upload_publish_and_link(meta, tmp_path):
    saved = {}
    v = _video(tmp_path)
    res = instagram.upload(_pub(), TOK, v, lambda st: saved.update(st))
    assert res.state == "processing" and saved["container_id"] == "C123" and saved["uploaded"]
    assert meta.uploaded == v.read_bytes()  # every byte, sent in chunks
    assert meta.upload_headers["content-length"] == str(v.stat().st_size)
    assert meta.upload_headers["authorization"] == "OAuth IGTOKEN" and meta.upload_headers["offset"] == "0"
    st = instagram.status(_pub(upload_state=saved), TOK)
    assert st["media_id"] == "M999" and meta.publish_count == 1
    done = instagram.status(_pub(upload_state={**saved, "media_id": "M999"}), TOK)
    assert done["state"] == "published" and done["url"].startswith("https://www.instagram.com/reel/")


def test_upload_does_not_read_the_whole_file_into_memory():
    import inspect

    src = inspect.getsource(instagram.upload)
    assert "f.read()" not in src and "_file_chunks" in src


def test_private_privacy_is_refused_before_any_request(meta, tmp_path):
    with pytest.raises(PublishError) as e:
        instagram.upload(_pub(privacy="private"), TOK, _video(tmp_path, 10), lambda st: None)
    assert not e.value.retryable and meta.calls == []


def test_authorization_error_code_190_marks_the_account(meta, tmp_path):
    meta.fail["/1789/media"] = httpx.Response(400, json={"error": {"message": "Error validating access token",
                                                                   "type": "OAuthException", "code": 190}})
    with pytest.raises(PublishError) as e:
        instagram.upload(_pub(), TOK, _video(tmp_path, 10), lambda st: None)
    assert e.value.auth and not e.value.retryable


def test_permission_and_rate_limit_errors_are_classified(meta, tmp_path):
    meta.fail["/1789/media"] = httpx.Response(400, json={"error": {"message": "(#10) permission", "code": 10}})
    with pytest.raises(PublishError) as e:
        instagram.upload(_pub(), TOK, _video(tmp_path, 10), lambda st: None)
    assert e.value.auth
    meta.fail["/1789/media"] = httpx.Response(400, json={"error": {"message": "limit", "code": 4}})
    with pytest.raises(PublishError) as e:
        instagram.upload(_pub(), TOK, _video(tmp_path, 10), lambda st: None)
    assert e.value.retryable and e.value.retry_after == 3600


def test_upload_failure_keeps_the_container_and_retries_without_a_new_one(meta, tmp_path):
    saved = {}
    meta.fail["rupload.facebook.com"] = httpx.Response(503, text="unavailable")
    with pytest.raises(PublishError) as e:
        instagram.upload(_pub(), TOK, _video(tmp_path), lambda st: saved.update(st))
    assert e.value.retryable and saved["container_id"] == "C123" and not saved.get("uploaded")
    meta.fail.clear()
    creates = sum(1 for m, u in meta.calls if m == "POST" and u.endswith("/1789/media"))
    instagram.upload(_pub(upload_state=dict(saved)), TOK, _video(tmp_path), lambda st: saved.update(st))
    assert sum(1 for m, u in meta.calls if m == "POST" and u.endswith("/1789/media")) == creates  # same container
    assert saved["uploaded"]


def test_rejected_video_fails_with_the_platform_reason(meta):
    meta.container_status = "ERROR"
    st = instagram.status(_pub(upload_state={"container_id": "C123", "ig_user_id": "1789"}), TOK)
    assert st["state"] == "failed" and "aspect ratio" in st["detail"] and meta.publish_count == 0


def test_already_published_container_is_never_published_twice(meta):
    meta.container_status = "PUBLISHED"
    meta.media_list = [{"id": "M999", "caption": "A fuga\n\nCréditos: X"}]
    st = instagram.status(_pub(upload_state={"container_id": "C123", "ig_user_id": "1789"}), TOK)
    assert st["media_id"] == "M999" and meta.publish_count == 0
    meta.media_list = []
    st = instagram.status(_pub(upload_state={"container_id": "C123", "ig_user_id": "1789"}), TOK)
    assert st["state"] == "failed" and meta.publish_count == 0


# ------------------------------------------------------------------ API: privacy and reasons
def _setup(tag, rights=True):
    with session_scope() as db:
        s = Streamer(platform="twitch", channel=f"ig_{tag}", url=f"https://www.twitch.tv/ig_{tag}",
                     preferences={"republish_allowed": rights})
        db.add(s)
        db.flush()
        sess = StreamSession(streamer_id=s.id, status="ended")
        db.add(sess)
        db.flush()
        c = Clip(session_id=sess.id, streamer_id=s.id, start=0, end=30, peak=10, score=80, status="approved",
                 title="A fuga", video_path="x.mp4", analysis_meta={"x": 1}, vision_state="done")
        db.add(c)
        a = PublishAccount(platform="instagram", external_id=f"ig_{tag}", name="malaio", status="connected",
                           connected_by="admin@asc.local",
                           tokens_enc=crypto.encrypt(TOK.as_dict()), meta={"privacy_options": ["public"], "flow": "unified"})
        db.add(a)
        db.flush()
        return c.id, a.id


def test_check_without_privacy_lists_every_other_reason_including_rights(admin):
    cid, aid = _setup("chk", rights=False)
    d = admin.get(f"/api/publish/clips/{cid}/check?account_id={aid}").json()
    assert d["privacy_required"] and d["privacy_options"] == ["public"]
    assert not any(p.startswith("privacidade") for p in d["problems"])  # not chosen yet: no verdict
    assert any(p.startswith("confirme") for p in d["problems"])  # rights reason shown
    d = admin.get(f"/api/publish/clips/{cid}/check?account_id={aid}&privacy=private").json()
    assert any("privacidade 'private'" in p for p in d["problems"])
    assert admin.get(f"/api/publish/clips/999999/check?account_id={aid}").json()["detail"].startswith("corte não encontrado")


def test_publication_privacy_must_be_explicit_and_valid(admin):
    cid, aid = _setup("pub")
    body = {"clip_id": cid, "account_id": aid, "title": "A fuga", "rights_confirmed": True}
    assert admin.post("/api/publish/publications", json=body, headers=CSRF).status_code == 422  # no silent default
    r = admin.post("/api/publish/publications", json={**body, "privacy": "private"}, headers=CSRF)
    assert r.status_code == 422 and "privacidade" in str(r.json())
    r = admin.post("/api/publish/publications", json={**body, "privacy": "public",
                                                       "scheduled_at": (utcnow() + timedelta(days=2)).isoformat() + "Z"},
                   headers=CSRF)
    assert r.status_code == 200 and r.json()["privacy"] == "public"  # what was chosen is what is stored


def test_rules_and_batch_refuse_a_privacy_the_account_does_not_have(admin):
    cid, aid = _setup("rule")
    r = admin.post("/api/publish/rules", json={"account_id": aid, "mode": "semi", "privacy": "private"}, headers=CSRF)
    assert r.status_code == 422 and "não existe nesta conta" in r.json()["detail"]
    r = admin.post("/api/publish/rules", json={"account_id": aid, "mode": "semi", "privacy": "public"}, headers=CSRF)
    assert r.status_code == 200  # semi: each Reel still needs a human confirmation
    r = admin.post("/api/publish/rules", json={"account_id": aid, "mode": "scheduled", "privacy": "public"}, headers=CSRF)
    assert r.status_code == 422  # fully automatic public still needs the explicit opt-in
    r = admin.post("/api/publish/batch", json={"clip_ids": [cid], "account_id": aid, "privacy": "private"}, headers=CSRF)
    assert r.status_code == 422
    assert admin.post("/api/publish/batch", json={"clip_ids": [cid], "account_id": aid}, headers=CSRF).status_code == 422


def test_existing_invalid_rule_is_flagged_in_the_overview(admin):
    from clipper.models import PublishRule

    _cid, aid = _setup("legacy")
    with session_scope() as db:
        db.add(PublishRule(account_id=aid, mode="semi", privacy="private", min_score=0))  # as saved by the old UI
    rules = [r for r in admin.get("/api/publish/overview").json()["rules"] if r["account_id"] == aid]
    assert rules and "não existe nesta conta" in rules[0]["problems"][0]
