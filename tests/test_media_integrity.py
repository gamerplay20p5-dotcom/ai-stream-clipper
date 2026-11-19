"""PR #2 review (6B): media integrity of variants, promotion and publications.

1. promotion carries sha256/size/render time/version, previous main metadata preserved
2. server-named, unique files: no delivery overwrites a referenced file
3. stale/out-of-order renders (variant or main) never replace a newer video; worker restart OK
4. a publication always sends the media snapshot it was created with
"""

from __future__ import annotations

import hashlib
from datetime import timedelta

import pytest
from sqlalchemy import select

from clipper import pipeline_structure as ps, storage
from clipper.db import session_scope, utcnow
from clipper.models import Clip, Publication, PublishAccount, Streamer, StreamSession
from clipper.publish import crypto, runner, service
from clipper.publish.base import Tokens, UploadResult
from clipper.settings_store import load_ai_settings
from conftest import CSRF, sign_headers


@pytest.fixture()
def store(tmp_path, monkeypatch):
    monkeypatch.setattr(storage, "root", lambda: tmp_path)
    return tmp_path


def _put(client, cid, data: bytes, **params):
    from urllib.parse import urlencode

    path = f"/internal/clips/{cid}/video?" + urlencode({"version": 1, **params})
    h = sign_headers("PUT", path, data, worker="206-render")
    h["x-asc-content-sha256"] = hashlib.sha256(data).hexdigest()
    return client.put(path, content=data, headers={**h, "Content-Type": "application/octet-stream"})


def _clip(store, tag, platform="youtube") -> tuple[int, int]:
    with session_scope() as db:
        s = Streamer(platform="twitch", channel=f"mi_{tag}", url=f"https://www.twitch.tv/mi_{tag}",
                     preferences={"republish_allowed": True})
        db.add(s)
        db.flush()
        sess = StreamSession(streamer_id=s.id, status="ended")
        db.add(sess)
        db.flush()
        c = Clip(session_id=sess.id, streamer_id=s.id, start=100, end=140, peak=130, score=80, status="approved",
                 kinds=["laughter"], version=1, analysis_meta={"x": 1}, vision_state="done", title="A fuga")
        db.add(c)
        db.flush()
        main = store / "clips" / str(c.id) / "clip_v1_aaaaaaaaaaaa.mp4"
        main.parent.mkdir(parents=True, exist_ok=True)
        main.write_bytes(b"MAIN-ORIGINAL" * 100)
        c.video_path = storage.rel(main) if hasattr(storage, "rel") else f"clips/{c.id}/clip_v1_aaaaaaaaaaaa.mp4"
        c.sha256, c.file_size, c.render_seconds = hashlib.sha256(main.read_bytes()).hexdigest(), 1300, 9.0
        a = PublishAccount(platform=platform, external_id=f"mi_{tag}", name=tag, status="connected",
                           connected_by="admin@asc.local",
                           tokens_enc=crypto.encrypt(Tokens("AT", "RT", utcnow() + timedelta(hours=1)).as_dict()),
                           meta={"privacy_options": ["private", "public"] if platform == "youtube" else ["public"]})
        db.add(a)
        db.flush()
        return c.id, a.id


def _variant(client, cid, body=b"EDITED-VERSION" * 50) -> str:
    with session_scope() as db:
        c = db.get(Clip, cid)
        ps.plan_variant(db, c, load_ai_settings(db), "D", manual=True)
        rid = c.provenance["variants"]["edit"]["render_id"]
    r = _put(client, cid, body, variant="edit", render_id=rid, seconds="7.5")
    assert r.status_code == 200 and r.json()["ok"], r.text
    return rid


# ------------------------------------------------------------------ 1 + 2
def test_promotion_carries_sha_size_time_and_preserves_the_previous_main(store, client, admin):
    cid, _ = _clip(store, "sha")
    _variant(client, cid)
    with session_scope() as db:
        before = db.get(Clip, cid)
        old = (before.video_path, before.sha256, before.file_size, before.version)
    r = admin.post(f"/api/clips/{cid}/variants/edit/promote", headers=CSRF)
    assert r.status_code == 200, r.text
    with session_scope() as db:
        c = db.get(Clip, cid)
        f = storage.resolve(c.video_path)
        assert hashlib.sha256(f.read_bytes()).hexdigest() == c.sha256 and c.file_size == f.stat().st_size
        assert c.render_seconds == 7.5 and c.version == old[3] + 1
        orig = c.provenance["variants"]["original"]
        assert (orig["video_path"], orig["sha256"], orig["size"], orig["version"]) == old
        assert storage.resolve(orig["video_path"]).exists()  # previous main kept on disk


def test_files_are_unique_per_render_and_never_overwrite_the_promoted_main(store, client, admin):
    cid, _ = _clip(store, "uniq")
    rid1 = _variant(client, cid, b"FIRST" * 40)
    admin.post(f"/api/clips/{cid}/variants/edit/promote", headers=CSRF)
    with session_scope() as db:
        main_path = db.get(Clip, cid).video_path
    assert rid1 in main_path
    rid2 = _variant(client, cid, b"SECOND" * 40)  # a new generation of the same variant name
    assert rid2 != rid1
    with session_scope() as db:
        c = db.get(Clip, cid)
        assert c.video_path == main_path and storage.resolve(main_path).read_bytes() == b"FIRST" * 40
        assert c.provenance["variants"]["edit"]["video_path"] != main_path


# ------------------------------------------------------------------ 3
def test_out_of_order_variant_renders_keep_the_newest(store, client):
    cid, _ = _clip(store, "ooo")
    with session_scope() as db:
        c = db.get(Clip, cid)
        ps.plan_variant(db, c, load_ai_settings(db), "D", manual=True)
        rid_old = c.provenance["variants"]["edit"]["render_id"]
        ps.plan_variant(db, c, load_ai_settings(db), "A", manual=True)  # newer request while the first renders
        rid_new = c.provenance["variants"]["edit"]["render_id"]
    assert _put(client, cid, b"NEW" * 30, variant="edit", render_id=rid_new).json()["ok"]
    late = _put(client, cid, b"OLD" * 30, variant="edit", render_id=rid_old)  # finishes after
    assert late.json() == {"ok": False, "variant": "edit", "stale": True}
    with session_scope() as db:
        rec = db.get(Clip, cid).provenance["variants"]["edit"]
        assert rec["structure"] == "A" and storage.resolve(rec["video_path"]).read_bytes() == b"NEW" * 30
    # worker restart: the same job delivers again with its own id -> accepted, same plan
    assert _put(client, cid, b"NEW" * 30, variant="edit", render_id=rid_new).json()["ok"]


def test_a_main_render_queued_before_a_promotion_is_stale(store, client, admin):
    from clipper.pipeline import enqueue_render

    cid, _ = _clip(store, "mainstale")
    with session_scope() as db:
        c = db.get(Clip, cid)
        job = enqueue_render(db, c)
        rid_main = job.payload["render_id"]
        c.status = "ready"  # e.g. the human promotes while that render is still queued
    _variant(client, cid)
    assert admin.post(f"/api/clips/{cid}/variants/edit/promote", headers=CSRF).status_code == 200
    with session_scope() as db:
        promoted = db.get(Clip, cid).video_path
    r = _put(client, cid, b"LATE-MAIN" * 20, render_id=rid_main)
    assert r.json().get("stale")
    with session_scope() as db:
        assert db.get(Clip, cid).video_path == promoted


# ------------------------------------------------------------------ 4 + publisher integration
class FakeProvider:
    def __init__(self):
        self.sent: list[bytes] = []

    def upload(self, snap, tokens, path, save_state):
        self.sent.append(path.read_bytes())
        return UploadResult(remote_id="R1", state="processing", remote_status={})


@pytest.mark.parametrize("platform,privacy", [("youtube", "private"), ("instagram", "public")])
def test_publication_sends_its_snapshot_even_after_a_promotion(store, client, admin, monkeypatch, platform, privacy):
    cid, aid = _clip(store, f"snap{platform}", platform)
    with session_scope() as db:
        c, a = db.get(Clip, cid), db.get(PublishAccount, aid)
        pub = service.create_publication(db, c, a, origin="manual", user="t@x", privacy=privacy, rights_confirmed=True,
                                         scheduled_at=utcnow() + timedelta(days=1))
        pid, approved_sha = pub.id, pub.video_sha256
        assert pub.video_path == c.video_path
    _variant(client, cid)
    r = admin.post(f"/api/clips/{cid}/variants/edit/promote", headers=CSRF)
    assert r.status_code == 200 and r.json()["promotion"]["pending_publications"] == 1
    assert "aprovado no agendamento" in r.json()["promotion"]["note"]
    fake = FakeProvider()
    monkeypatch.setattr(service, "provider", lambda p: fake)
    monkeypatch.setattr(service, "tokens_for", lambda db, acct: Tokens("AT", "", None, "", {}))
    monkeypatch.setattr(runner, "MIN_GAP_BETWEEN_UPLOADS_S", 0)
    runner.run_publish(0, pid)
    assert fake.sent == [b"MAIN-ORIGINAL" * 100]  # the approved/scheduled file, not the promoted one
    with session_scope() as db:
        p = db.get(Publication, pid)
        assert p.status in ("processing", "published") and p.video_sha256 == approved_sha


def test_promotion_is_blocked_while_a_publication_is_uploading(store, client, admin):
    cid, aid = _clip(store, "busy")
    _variant(client, cid)
    with session_scope() as db:
        db.add(Publication(clip_id=cid, account_id=aid, platform="youtube", status="uploading", privacy="private"))
    r = admin.post(f"/api/clips/{cid}/variants/edit/promote", headers=CSRF)
    assert r.status_code == 409 and "sendo enviada" in r.json()["detail"]


def test_old_publication_without_snapshot_refuses_a_changed_video(store, client, monkeypatch):
    cid, aid = _clip(store, "legacy")
    with session_scope() as db:
        pub = Publication(clip_id=cid, account_id=aid, platform="youtube", status="scheduled", privacy="private",
                          video_sha256="0" * 64, video_path="")  # created before v0.6.1, video changed since
        db.add(pub)
        db.flush()
        pid = pub.id
    fake = FakeProvider()
    monkeypatch.setattr(service, "provider", lambda p: fake)
    monkeypatch.setattr(service, "tokens_for", lambda db, acct: Tokens("AT", "", None, "", {}))
    runner.run_publish(0, pid)
    with session_scope() as db:
        p = db.get(Publication, pid)
        assert fake.sent == [] and p.status == "failed" and "mudou depois" in p.last_error


def test_one_malformed_publication_never_stops_status_polling(store, client, monkeypatch):
    cid, aid = _clip(store, "poll")
    with session_scope() as db:
        bad = Publication(clip_id=cid, account_id=aid, platform="youtube", status="processing", privacy="private")
        good = Publication(clip_id=None, account_id=aid, platform="youtube", status="processing", privacy="private",
                           remote_id="OK1")
        db.add_all([bad, good])
        db.flush()
        bad_id, good_id = bad.id, good.id

    class P:
        def status(self, snap, tokens):
            if snap.remote_id != "OK1":
                raise KeyError("container_id")  # a provider bug on one row
            return {"state": "published", "url": "https://youtu.be/OK1"}

    monkeypatch.setattr(service, "provider", lambda p: P())
    monkeypatch.setattr(service, "tokens_for", lambda db, acct: Tokens("AT", "", None, "", {}))
    runner.poll_processing()
    with session_scope() as db:
        assert db.get(Publication, good_id).status == "published"
        assert "consulta de status falhou" in db.get(Publication, bad_id).last_error


def test_snapshot_of_an_uncertain_publication_is_never_deleted(store, client):
    """Integration of PR #2 (snapshots) with PR #3 (uncertain status): the media of a publication
    whose result is uncertain stays referenced (it may be needed to reconcile or explain)."""
    cid, aid = _clip(store, "unc")
    with session_scope() as db:
        c = db.get(Clip, cid)
        db.add(Publication(clip_id=cid, account_id=aid, platform="instagram", status="uncertain", privacy="public",
                           video_path="clips/x/clip_v1_snapshot.mp4"))
        db.flush()
        assert "clips/x/clip_v1_snapshot.mp4" in ps.referenced_files(db, c)
