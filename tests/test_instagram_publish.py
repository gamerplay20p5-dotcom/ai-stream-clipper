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


# ------------------------------------------------------------------ PR #3 review: uncertain outcomes
def _iso(dt):
    return dt.strftime("%Y-%m-%dT%H:%M:%S+0000")


def _reel(mid, caption="A fuga\n\nCréditos: X", minutes=1, kind="REELS"):
    from datetime import datetime, timedelta as td, timezone

    return {"id": mid, "caption": caption, "media_product_type": kind,
            "timestamp": _iso(datetime.now(timezone.utc) + td(minutes=minutes))}


def _called(minutes_ago=0):
    from datetime import datetime, timedelta as td, timezone

    return (datetime.now(timezone.utc) - td(minutes=minutes_ago)).isoformat()


def _timeout_on_publish(meta):
    def handler(req):
        if str(req.url).endswith("/media_publish"):
            meta.publish_count += 1
            raise httpx.ReadTimeout("no answer")
        return meta.handler(req)
    return lambda: httpx.Client(transport=httpx.MockTransport(handler))


def test_publish_attempt_is_saved_before_media_publish_and_a_timeout_is_uncertain(meta, monkeypatch):
    saved = []
    monkeypatch.setattr(instagram, "_client", _timeout_on_publish(meta))
    st = instagram.status(_pub(upload_state={"container_id": "C123", "ig_user_id": "1789"}), TOK, saved.append)
    assert saved and saved[0].get("publish_called_at")  # recorded BEFORE the call
    assert st["state"] == "uncertain" and meta.publish_count == 1


def test_lost_answer_then_published_container_without_a_match_stays_uncertain(meta):
    meta.container_status = "PUBLISHED"
    meta.media_list = []
    state = {"container_id": "C123", "ig_user_id": "1789", "publish_called_at": _called()}
    st = instagram.status(_pub(upload_state=state), TOK, lambda s: None)
    assert st["state"] == "uncertain" and meta.publish_count == 0  # never publishes again


def test_reconciliation_needs_full_caption_reel_type_and_attempt_window(meta):
    meta.container_status = "PUBLISHED"
    state = {"container_id": "C123", "ig_user_id": "1789", "publish_called_at": _called()}
    cap = "A fuga\n\nCréditos: X"
    meta.media_list = [_reel("WRONG1", cap + " (outro)"),     # same prefix, different caption
                       _reel("WRONG2", cap, minutes=-90),     # too old: another post
                       _reel("WRONG3", cap, kind="FEED"),     # not a Reel
                       _reel("M999", cap)]
    st = instagram.status(_pub(upload_state=state), TOK, lambda s: None)
    assert st.get("media_id") == "M999" and meta.publish_count == 0
    meta.media_list.append(_reel("M1000", cap, minutes=3))  # two plausible Reels
    st = instagram.status(_pub(upload_state=state), TOK, lambda s: None)
    assert st["state"] == "uncertain" and "ambígua" in st["detail"]


def test_without_a_recorded_attempt_a_caption_match_is_not_enough(meta):
    meta.container_status = "PUBLISHED"
    meta.media_list = [_reel("M999")]
    st = instagram.status(_pub(upload_state={"container_id": "C123", "ig_user_id": "1789"}), TOK, lambda s: None)
    assert st["state"] == "uncertain" and meta.publish_count == 0


def test_unanswered_call_on_a_still_unpublished_container_is_retried_only_after_a_while(meta):
    saved = []
    recent = {"container_id": "C123", "ig_user_id": "1789", "publish_called_at": _called(1)}
    st = instagram.status(_pub(upload_state=recent), TOK, saved.append)
    assert st["state"] == "processing" and meta.publish_count == 0  # Meta may still be publishing
    old = {**recent, "publish_called_at": _called(15)}
    st = instagram.status(_pub(upload_state=old), TOK, saved.append)
    assert st["state"] == "processing" and "publish_called_at" not in saved[-1] and meta.publish_count == 0


def test_clear_refusal_of_media_publish_is_not_uncertain(meta):
    saved = []
    meta.fail["/media_publish"] = httpx.Response(400, json={"error": {"message": "media not ready", "code": 9007}})
    with pytest.raises(PublishError):
        instagram.status(_pub(upload_state={"container_id": "C123", "ig_user_id": "1789"}), TOK, saved.append)
    assert saved and "publish_called_at" not in saved[-1]  # Meta said no: nothing published


def test_server_error_on_media_publish_is_uncertain(meta):
    meta.fail["/media_publish"] = httpx.Response(502, text="bad gateway")
    st = instagram.status(_pub(upload_state={"container_id": "C123", "ig_user_id": "1789"}), TOK, lambda s: None)
    assert st["state"] == "uncertain"


def test_rejected_video_is_a_safe_failure(meta):
    meta.container_status = "ERROR"
    st = instagram.status(_pub(upload_state={"container_id": "C123", "ig_user_id": "1789"}), TOK)
    assert st["state"] == "failed" and st["failure"] == "rejected_by_platform"


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


# ------------------------------------------------------------------ end to end through the runner
def test_manual_instagram_flow_and_the_duplicate_after_an_uncertain_result(admin, meta, monkeypatch, tmp_path):
    from clipper import queue, storage
    from clipper.models import Publication
    from clipper.publish import runner, service

    cid, aid = _setup("e2e")
    v = _video(tmp_path, 200_000)
    monkeypatch.setattr(storage, "resolve", lambda rel: v)
    monkeypatch.setattr(service, "tokens_for", lambda db, acct: TOK)
    monkeypatch.setattr(runner, "MIN_GAP_BETWEEN_UPLOADS_S", 0)
    with session_scope() as db:
        db.get(Clip, cid).sha256 = ""
    body = {"clip_id": cid, "account_id": aid, "title": "A fuga", "description": "Créditos: X", "privacy": "public",
            "rights_confirmed": True}
    r = admin.post("/api/publish/publications", json=body, headers=CSRF)
    assert r.status_code == 200 and r.json()["privacy"] == "public"
    pid = r.json()["id"]
    with session_scope() as db:
        jobs = queue.claim(db, runner.worker_id(), ["publish"], has_gpu=False, limit=20, lease_seconds=600)
        jid = next(j.id for j in jobs if j.payload["publication_id"] == pid)
        for j in jobs:
            if j.id != jid:
                queue.complete(db, j.id, runner.worker_id(), {})
    runner.run_publish(jid, pid)  # container + streamed upload
    with session_scope() as db:
        assert db.get(Publication, pid).status == "processing"
    # 1st poll: Meta publishes, but the answer is lost
    monkeypatch.setattr(instagram, "_client", _timeout_on_publish(meta))
    runner.poll_processing()
    with session_scope() as db:
        p = db.get(Publication, pid)
        assert p.status == "uncertain" and p.upload_state.get("publish_called_at")
    # 2nd poll: container PUBLISHED but the Reel is not found -> still uncertain, no second publish
    meta.container_status, meta.media_list = "PUBLISHED", []
    runner.poll_processing()
    assert meta.publish_count == 1
    # the person tries to publish the same clip again: refused, nothing erased
    r = admin.post("/api/publish/publications", json=body, headers=CSRF)
    assert r.status_code in (409, 422) and "incerto" in str(r.json())
    assert admin.post(f"/api/publish/publications/{pid}/retry", headers=CSRF).status_code == 409
    with session_scope() as db:
        assert db.get(Publication, pid).upload_state.get("container_id") == "C123"
    # a later poll finds exactly one matching Reel -> confirmed, still a single media_publish
    meta.media_list = [_reel("M999")]
    runner.poll_processing()
    runner.poll_processing()
    with session_scope() as db:
        p = db.get(Publication, pid)
        assert p.status == "published" and p.remote_url.startswith("https://www.instagram.com/reel/")
    assert meta.publish_count == 1


def test_person_resolves_an_uncertain_publication(admin):
    from clipper.models import Publication

    cid, aid = _setup("resolve")
    with session_scope() as db:
        p = Publication(clip_id=cid, account_id=aid, platform="instagram", status="uncertain", privacy="public",
                        upload_state={"container_id": "C9", "publish_called_at": _called()})
        db.add(p)
        db.flush()
        pid = p.id
    body = {"clip_id": cid, "account_id": aid, "title": "x", "privacy": "public", "rights_confirmed": True,
            "scheduled_at": (utcnow() + timedelta(days=3)).isoformat() + "Z"}
    assert admin.post("/api/publish/publications", json=body, headers=CSRF).status_code in (409, 422)
    bad = admin.post(f"/api/publish/publications/{pid}/resolve", json={"outcome": "published", "url": "javascript:x"},
                     headers=CSRF)
    assert bad.status_code == 422
    r = admin.post(f"/api/publish/publications/{pid}/resolve", json={"outcome": "not_published"}, headers=CSRF)
    assert r.status_code == 200 and r.json()["status"] == "failed"
    r = admin.post("/api/publish/publications", json=body, headers=CSRF)
    assert r.status_code == 200  # now safe: the person confirmed nothing was published


def test_automatic_public_publishing_stays_disabled(admin):
    s = admin.get("/api/settings").json()
    assert s["auto_publish"] is False
    cid, aid = _setup("auto")
    r = admin.post("/api/publish/rules", json={"account_id": aid, "mode": "immediate", "privacy": "public"}, headers=CSRF)
    assert r.status_code == 422  # fully automatic public needs the explicit opt-in (not given)


def test_clip_with_an_uncertain_publication_is_protected_from_cleanup(admin):
    from clipper import storage_lifecycle
    from clipper.models import Publication

    cid, aid = _setup("lc")
    with session_scope() as db:
        db.add(Publication(clip_id=cid, account_id=aid, platform="instagram", status="uncertain", privacy="public"))
        db.flush()
        why = storage_lifecycle.protection(db, db.get(Clip, cid))
        assert "publicado ou agendado" in why
