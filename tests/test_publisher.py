"""Phase 4 auto publisher: crypto, YouTube resumable upload (no duplicates), eligibility, schedule, OAuth, runner."""

from __future__ import annotations

import json
from datetime import timedelta
from pathlib import Path

import httpx
import pytest
from sqlalchemy import select

from clipper.db import session_scope, utcnow
from clipper.models import Clip, Publication, PublishAccount, PublishRule, Streamer, StreamSession
from clipper.publish import crypto, runner, service, youtube
from clipper.publish.base import PublishError, Tokens
from conftest import CSRF


# ------------------------------------------------------------------ fake YouTube
class FakeYouTube:
    def __init__(self):
        self.inits = 0
        self.received = 0
        self.size = 0
        self.fail_after_first_chunk = False
        self.completed = False
        self.video_id = "vid12345678"
        self.processing = "processing"

    def handler(self, req: httpx.Request) -> httpx.Response:
        url = str(req.url)
        if url.startswith(youtube.TOKEN_URL):
            body = dict(x.split("=", 1) for x in req.content.decode().split("&"))
            if body["grant_type"] == "authorization_code":
                assert body["code_verifier"]  # PKCE
                return httpx.Response(200, json={"access_token": "AT1", "refresh_token": "RT1", "expires_in": 3600,
                                                 "scope": " ".join(youtube.SCOPES)})
            return httpx.Response(200, json={"access_token": "AT2", "expires_in": 3600})
        if "/youtube/v3/channels" in url:
            return httpx.Response(200, json={"items": [{"id": "UCchan", "snippet": {"title": "Meu Canal", "customUrl": "@meu"}}]})
        if url.startswith(youtube.UPLOAD) and req.method == "POST":
            self.inits += 1
            meta = json.loads(req.content)
            assert meta["status"]["privacyStatus"] in youtube.PRIVACY and meta["snippet"]["categoryId"] == "20"
            self.size = int(req.headers["x-upload-content-length"])
            return httpx.Response(200, headers={"Location": "https://upload.example/session/1"})
        if url.startswith("https://upload.example/session/1") and req.method == "PUT":
            cr = req.headers.get("content-range", "")
            if cr.startswith("bytes */"):  # resume query
                if self.completed:
                    return httpx.Response(200, json={"id": self.video_id, "status": {"uploadStatus": "uploaded"}})
                return httpx.Response(308, headers={"Range": f"bytes=0-{self.received - 1}"} if self.received else {})
            start, rest = cr.split(" ")[1].split("-")
            end, total = rest.split("/")
            assert int(start) == self.received, "resumed at the wrong offset"
            self.received = int(end) + 1
            if self.fail_after_first_chunk:
                self.fail_after_first_chunk = False
                raise httpx.ConnectError("network down")
            if self.received >= int(total):
                self.completed = True
                return httpx.Response(201, json={"id": self.video_id, "status": {"uploadStatus": "uploaded",
                                                                                 "privacyStatus": "private"}})
            return httpx.Response(308, headers={"Range": f"bytes=0-{self.received - 1}"})
        if "/youtube/v3/videos" in url:
            part = req.url.params.get("part")
            if part == "statistics":
                return httpx.Response(200, json={"items": [{"statistics": {"viewCount": "42", "likeCount": "7"}}]})
            return httpx.Response(200, json={"items": [{"status": {"uploadStatus": self.processing,
                                                                   "privacyStatus": "private"},
                                                        "processingDetails": {"processingStatus": "succeeded"
                                                                              if self.processing == "processed" else "processing"}}]})
        return httpx.Response(404, json={"error": url})


@pytest.fixture()
def fake_yt(monkeypatch):
    fy = FakeYouTube()
    monkeypatch.setattr(youtube, "_client", lambda: httpx.Client(transport=httpx.MockTransport(fy.handler)))
    monkeypatch.setattr(youtube, "CHUNK", 256 * 1024)
    return fy


def _video(tmp_path: Path, size=600 * 1024) -> Path:
    p = tmp_path / "v.mp4"
    p.write_bytes(b"\0" * size)
    return p


def test_crypto_roundtrip_and_tamper():
    tok = crypto.encrypt({"access_token": "secret-value"})
    assert "secret-value" not in tok
    assert crypto.decrypt(tok)["access_token"] == "secret-value"
    assert crypto.decrypt(crypto.encrypt("plain")) == "plain"
    bad = tok[:-4] + ("AAAA" if not tok.endswith("AAAA") else "BBBB")
    with pytest.raises(crypto.CryptoError):
        crypto.decrypt(bad)


def _pub_ns(**kw):
    from types import SimpleNamespace

    base = dict(id=1, title="Teste #Shorts", description="d", tags=["gta"], privacy="private", upload_state={},
                remote_id="", remote_status={})
    base.update(kw)
    return SimpleNamespace(**base)


def test_youtube_resumable_upload_and_crash_resume_never_duplicates(fake_yt, tmp_path):
    video = _video(tmp_path)
    saved = {}
    fake_yt.fail_after_first_chunk = True
    pub = _pub_ns()
    with pytest.raises(httpx.ConnectError):
        youtube.upload(pub, Tokens("AT"), video, lambda st: saved.update(st))
    assert saved["session_uri"] and fake_yt.inits == 1
    # restart: same publication, saved session -> resumes from the server offset, no new insert
    pub.upload_state = dict(saved)
    res = youtube.upload(pub, Tokens("AT"), video, lambda st: saved.update(st))
    assert res.remote_id == fake_yt.video_id and fake_yt.inits == 1
    assert res.remote_url.endswith("/shorts/" + fake_yt.video_id)
    # crash AFTER completion but before we stored the id: the session query recovers the video
    res2 = youtube.upload(pub, Tokens("AT"), video, lambda st: None)
    assert res2.remote_id == fake_yt.video_id and fake_yt.inits == 1


def _setup(tag: str, *, rights=True, status="approved", is_test=False, score=80.0, platform="youtube"):
    with session_scope() as db:
        s = Streamer(platform="twitch", channel=f"pb_{tag}", url=f"https://www.twitch.tv/pb_{tag}", display_name=f"PB {tag}",
                     preferences={"republish_allowed": rights})
        db.add(s)
        db.flush()
        sess = StreamSession(streamer_id=s.id, status="live")
        db.add(sess)
        db.flush()
        c = Clip(session_id=sess.id, streamer_id=s.id, start=10, end=40, peak=20, score=score, status=status,
                 is_test=is_test, title="Fuga da polícia", description="Perseguição", hashtags=["gta", "rp"],
                 video_path="clips/x/video.mp4", analysis_meta={"vision_provider": "openvino-vlm:qwen"},
                 vision_state="done")
        db.add(c)
        a = PublishAccount(platform=platform, external_id=f"ext_{tag}", name=f"Conta {tag}", status="connected",
                           tokens_enc=crypto.encrypt(Tokens("AT", "RT", utcnow() + timedelta(hours=1)).as_dict()),
                           meta={"privacy_options": list(youtube.PRIVACY) if platform == "youtube" else ["SELF_ONLY"]})
        db.add(a)
        db.flush()
        return c.id, a.id, s.id


def test_eligibility_gates(client):
    with session_scope() as db:
        cid, aid, _ = _setup("gates", rights=False, status="ready", is_test=True, score=50)
        clip, acct = db.get(Clip, cid), db.get(PublishAccount, aid)
        rule = PublishRule(account_id=aid, mode="scheduled", min_score=70, require_approval=True, privacy="public",
                           allow_public_auto=False)
        db.add(rule)
        db.flush()
        p = " | ".join(service.eligibility(db, clip, acct, auto=True, rule=rule, privacy="public"))
        assert "TESTE MANUAL" in p and "direito de republicação" in p and "aprovação humana" in p
        assert "abaixo do mínimo" in p and "PÚBLICA automática" in p
        # manual: explicit rights confirmation required when not on file
        assert any("confirme" in x for x in service.eligibility(db, clip, acct, auto=False))
        clip.status = "rejected"
        assert any("rejeitado" in x for x in service.eligibility(db, clip, acct, auto=False, rights_confirmed=True))
        clip.status, clip.vision_state, clip.is_test = "ready", "pending", False
        assert any("análise incompleta" in x for x in service.eligibility(db, clip, acct, auto=False, rights_confirmed=True))


def test_tiktok_is_manual_only(client, admin):
    with session_scope() as db:
        cid, aid, _ = _setup("tt", platform="tiktok")
        clip, acct = db.get(Clip, cid), db.get(PublishAccount, aid)
        rule = PublishRule(account_id=aid, mode="immediate", min_score=0, require_approval=False, privacy="SELF_ONLY")
        assert any("TikTok" in x for x in service.eligibility(db, clip, acct, auto=True, rule=rule, privacy="SELF_ONLY"))
    r = admin.post("/api/publish/rules", json={"account_id": aid, "mode": "scheduled", "privacy": "SELF_ONLY"}, headers=CSRF)
    assert r.status_code == 422 and "TikTok" in r.json()["detail"]


def test_schedule_respects_slots_and_daily_limit(client):
    with session_scope() as db:
        _, aid, _ = _setup("slots")
        acct = db.get(PublishAccount, aid)
        rule = PublishRule(account_id=aid, mode="scheduled", slots=["12:00", "18:00"], max_per_day=1,
                           timezone="America/Sao_Paulo")
        db.add(rule)
        db.flush()
        s1 = service.next_slot(db, acct, rule)
        db.add(Publication(clip_id=None, account_id=aid, platform="youtube", status="scheduled", scheduled_at=s1))
        db.flush()
        s2 = service.next_slot(db, acct, rule)
        assert s2 > s1 and (s2 - s1) >= timedelta(hours=6)  # same day is full (max 1/day)


def test_publication_is_unique_per_clip_and_account(client):
    with session_scope() as db:
        cid, aid, _ = _setup("uniq")
        clip, acct = db.get(Clip, cid), db.get(PublishAccount, aid)
        p1 = service.create_publication(db, clip, acct, origin="manual", user="t@x")
        p2 = service.create_publication(db, clip, acct, origin="manual", user="t@x")
        assert p1.id == p2.id
        meta = service.suggest_metadata(db, clip, "youtube")
        assert "#Shorts" in meta["title"] + meta["description"] and "Créditos" in meta["description"]


def test_oauth_flow_state_is_single_use_and_tokens_never_returned(admin, fake_yt):
    r = admin.put("/api/publish/apps/youtube", json={"client_id": "cid-123.apps.googleusercontent.com",
                                                     "client_secret": "super-secret", "redirect_uri": "http://127.0.0.1:8765/"},
                  headers=CSRF)
    assert r.status_code == 200
    start = admin.post("/api/publish/oauth/youtube/start", headers=CSRF).json()
    assert "code_challenge=" in start["auth_url"] and "access_type=offline" in start["auth_url"]
    url = f"http://127.0.0.1:8765/?state={start['state']}&code=4/abc"
    acct = admin.post("/api/publish/oauth/youtube/finish", json={"redirected_url": url}, headers=CSRF)
    assert acct.status_code == 200, acct.text
    assert acct.json()["external_id"] == "UCchan" and acct.json()["status"] == "connected"
    again = admin.post("/api/publish/oauth/youtube/finish", json={"redirected_url": url}, headers=CSRF)
    assert again.status_code == 422  # replay refused
    ov = admin.get("/api/publish/overview").text
    assert "super-secret" not in ov and "RT1" not in ov and "AT1" not in ov and "tokens_enc" not in ov


def test_runner_uploads_polls_and_collects_metrics(client, fake_yt, tmp_path, monkeypatch):
    from clipper import storage

    monkeypatch.setattr(storage, "resolve", lambda rel: _video(tmp_path))
    with session_scope() as db:
        cid, aid, _ = _setup("run")
        clip, acct = db.get(Clip, cid), db.get(PublishAccount, aid)
        clip.sha256 = ""
        pub = service.create_publication(db, clip, acct, origin="manual", user="t@x", privacy="private")
        service.enqueue_due(db)
        pid = pub.id
    from clipper import queue

    with session_scope() as db:
        jobs = queue.claim(db, runner.worker_id(), ["publish"], has_gpu=False, limit=5, lease_seconds=600)
        mine = [j for j in jobs if j.payload["publication_id"] == pid]
        assert mine
        jid = mine[0].id
        for j in jobs:
            if j.id != jid:
                queue.complete(db, j.id, runner.worker_id(), {})
    runner.run_publish(jid, pid)
    with session_scope() as db:
        pub = db.get(Publication, pid)
        assert pub.status == "processing" and pub.remote_id == fake_yt.video_id and fake_yt.inits == 1
        q = db.get(PublishAccount, aid).quota
        assert q["uploads"] == 1 and q["units"] == 0  # 1 call in the Video Uploads bucket
    fake_yt.processing = "processed"
    runner.poll_processing()
    runner.refresh_metrics([pid])
    with session_scope() as db:
        pub = db.get(Publication, pid)
        assert pub.status == "published" and pub.published_at is not None
        assert pub.metrics["viewCount"] == 42
        assert [e["event"] for e in pub.log][:3] == ["created", "upload", "uploaded"]


def test_auth_failure_marks_account_and_never_loops(client, monkeypatch, tmp_path):
    with session_scope() as db:
        cid, aid, _ = _setup("auth")
        acct = db.get(PublishAccount, aid)
        acct.tokens_enc = crypto.encrypt(Tokens("AT", "RT", utcnow() - timedelta(hours=1)).as_dict())  # expired

    def bad_refresh(app, tokens):
        raise PublishError("revogado", retryable=False, auth=True)

    monkeypatch.setattr(youtube, "refresh", bad_refresh)
    monkeypatch.setattr(service, "get_app", lambda db, p: None)
    with session_scope() as db:
        acct = db.get(PublishAccount, aid)
        with pytest.raises(PublishError):
            service.tokens_for(db, acct)
        assert acct.status == "expired"


def test_youtube_quota_uses_the_upload_bucket_and_privacy_is_always_explicit():
    from clipper.publish import runner as rn

    a = PublishAccount(platform="youtube", external_id="q", meta={"upload_limit": 100},
                       quota={"day": "2000-01-01", "units": 1601})  # pre-0.4.5 format, old day
    assert rn._quota(a)["uploads"] == 0 and rn._youtube_quota_ok(a)
    for _ in range(99):
        rn._add_quota(a, uploads=1)
    assert rn._youtube_quota_ok(a)  # 99 used: the 100th is still allowed
    rn._add_quota(a, uploads=1)
    assert not rn._youtube_quota_ok(a)
    rn._add_quota(a, units=5)
    assert a.quota["units"] == 5 and a.quota["uploads"] == 100
    for priv in ("private", "unlisted", "public"):  # the API default became public: we always send it
        assert youtube._metadata(_pub_ns(privacy=priv))["status"]["privacyStatus"] == priv
    sched = youtube._metadata(_pub_ns(privacy="public", upload_state={"publish_at": "2026-12-01T15:00:00Z"}))
    assert sched["status"] == {**sched["status"], "privacyStatus": "private", "publishAt": "2026-12-01T15:00:00Z"}


def test_disconnect_and_stale_metrics_delete_platform_data(admin, client):
    from clipper import maintenance

    cid, aid, _ = _setup("purge")
    _, aid2, _ = _setup("stale")
    with session_scope() as db:
        p1 = Publication(clip_id=cid, account_id=aid, platform="youtube", status="published",
                         metrics={"viewCount": 5}, metrics_at=utcnow())
        db.add(p1)
        db.flush()
        pid = p1.id
        old = Publication(clip_id=None, account_id=aid2, platform="youtube", status="published",
                          metrics={"viewCount": 9}, metrics_at=utcnow() - timedelta(days=31))
        db.add(old)
        db.flush()
        oid = old.id
    assert admin.delete(f"/api/publish/accounts/{aid}", headers=CSRF).status_code == 200
    maintenance.retention()
    with session_scope() as db:
        assert db.get(Publication, pid).metrics == {} and db.get(PublishAccount, aid).tokens_enc == ""
        assert db.get(Publication, oid).metrics == {}
