"""Phase 5 stage C: score-based storage lifecycle (simulation, protection, quarantine, purge, restore)."""

from __future__ import annotations

from datetime import timedelta

import pytest

from clipper import storage, storage_lifecycle as lc
from clipper.db import session_scope, utcnow
from clipper.models import CleanupLog, Clip, EvalExample, Publication, PublishAccount, Streamer, StreamSession
from clipper.settings_store import AISettings
from conftest import CSRF


@pytest.fixture()
def store(tmp_path, monkeypatch):
    monkeypatch.setattr(storage, "root", lambda: tmp_path)
    return tmp_path


def _clip(store, tag, *, status="ready", score=30.0, age_days=10, files=("video_v1.mp4", "source.mp4", "thumb_v1.jpg"),
          **kw) -> int:
    with session_scope() as db:
        s = Streamer(platform="twitch", channel=f"lc_{tag}", url=f"https://www.twitch.tv/lc_{tag}", display_name=tag)
        db.add(s)
        db.flush()
        sess = StreamSession(streamer_id=s.id, status="ended")
        db.add(sess)
        db.flush()
        c = Clip(session_id=sess.id, streamer_id=s.id, start=0, end=30, peak=10, score=score, status=status,
                 video_path="x", **kw)
        db.add(c)
        db.flush()
        old = utcnow() - timedelta(days=age_days)
        c.created_at = c.updated_at = old
        d = store / "clips" / str(c.id)
        d.mkdir(parents=True, exist_ok=True)
        for f in files:
            (d / f).write_bytes(b"x" * 1000)
        return c.id


def _settings(**kw):
    base = dict(cleanup_enabled=True, cleanup_dry_run=False, cleanup_keep_min_score=50, cleanup_below_min_days=3,
                cleanup_rejected_days=7, cleanup_quarantine_days=7)
    base.update(kw)
    return AISettings(**base)


def _authorize_all(db):
    lc.save_state(db, {"enabled_at": (utcnow() - timedelta(days=60)).isoformat(), "retro_authorized": True})


def test_simulation_never_changes_anything(store, client):
    cid = _clip(store, "sim")
    with session_scope() as db:
        _authorize_all(db)
        p = lc.plan(db, _settings())
        assert any(i["clip_id"] == cid for i in p["eligible"]) and p["total_bytes"] >= 3000
        out = lc.run(db, _settings(cleanup_dry_run=True))
        assert out["dry_run"] and out["quarantined"] == 0
    assert (store / "clips" / str(cid) / "video_v1.mp4").exists()
    with session_scope() as db:
        assert db.get(Clip, cid).lifecycle == "active"


def test_existing_clips_need_retroactive_authorisation(store, client):
    cid = _clip(store, "retro")
    with session_scope() as db:
        lc.save_state(db, {"enabled_at": utcnow().isoformat()})  # enabled now: this clip is older
        p = lc.plan(db, _settings())
        assert not any(i["clip_id"] == cid for i in p["eligible"])
        assert any(i["clip_id"] == cid and "anterior" in i["protected_by"][0] for i in p["protected"])


def test_below_min_score_goes_to_quarantine_then_purge_keeping_the_row(store, client):
    cid = _clip(store, "low", score=20)
    with session_scope() as db:
        _authorize_all(db)
        assert lc.run(db, _settings())["quarantined"] >= 1
        c = db.get(Clip, cid)
        assert c.lifecycle == "quarantined" and c.purge_after > utcnow()
    assert (store / "clips" / str(cid) / "video_v1.mp4").exists()  # still recoverable
    with session_scope() as db:
        db.get(Clip, cid).purge_after = utcnow() - timedelta(seconds=1)
    with session_scope() as db:
        out = lc.run(db, _settings())
        assert out["purged"] >= 1 and out["freed"] >= 3000
    with session_scope() as db:
        c = db.get(Clip, cid)
        assert c is not None and c.lifecycle == "purged" and c.video_path == ""  # row kept
        assert db.query(CleanupLog).filter(CleanupLog.clip_id == cid, CleanupLog.action == "purge_video").count() == 1
    assert not (store / "clips" / str(cid) / "video_v1.mp4").exists()


def test_protected_favorite_approved_published_and_busy_are_never_touched(store, client):
    ids = {
        "protected": _clip(store, "p1", protected=True),
        "favorite": _clip(store, "p2", favorite=True),
        "approved": _clip(store, "p3", status="approved"),
        "busy": _clip(store, "p4", status="waiting"),
        "published": _clip(store, "p5"),
    }
    with session_scope() as db:
        a = PublishAccount(platform="youtube", external_id="lc-acc", status="connected")
        db.add(a)
        db.flush()
        db.add(Publication(clip_id=ids["published"], account_id=a.id, platform="youtube", status="published"))
        _authorize_all(db)
        lc.run(db, _settings())
        for cid in ids.values():
            assert db.get(Clip, cid).lifecycle == "active"
        reasons = {i["clip_id"]: i["protected_by"] for i in lc.plan(db, _settings())["protected"]}
        assert "publicado ou agendado" in reasons[ids["published"]]
        assert "protegido manualmente" in reasons[ids["protected"]] and "favorito" in reasons[ids["favorite"]]


def test_source_still_needed_by_the_dataset_is_protected(store, client):
    cid = _clip(store, "shared", status="rejected")
    with session_scope() as db:
        db.add(EvalExample(clip_id=cid, stratum="accepted", media_state="pending"))
        _authorize_all(db)
        p = lc.plan(db, _settings())
        assert any(i["clip_id"] == cid and "dataset" in " ".join(i["protected_by"]) for i in p["protected"])


def test_restore_from_quarantine_and_approval_during_quarantine_cancels_purge(store, client):
    a, b = _clip(store, "rest"), _clip(store, "appr")
    with session_scope() as db:
        _authorize_all(db)
        lc.run(db, _settings())
        assert lc.restore(db, a, "t@x") and db.get(Clip, a).lifecycle == "active"
        clip_b = db.get(Clip, b)
        clip_b.status, clip_b.purge_after = "approved", utcnow() - timedelta(seconds=1)
    with session_scope() as db:
        assert lc.purge(db, b, _settings()) == 0
        assert db.get(Clip, b).lifecycle == "active"  # approved meanwhile: nothing deleted
    assert (store / "clips" / str(b) / "video_v1.mp4").exists()


def test_purge_interrupted_by_restart_is_idempotent(store, client):
    cid = _clip(store, "intr")
    with session_scope() as db:
        _authorize_all(db)
        lc.run(db, _settings())
        db.get(Clip, cid).purge_after = utcnow() - timedelta(seconds=1)
    (store / "clips" / str(cid) / "video_v1.mp4").unlink()  # crash after deleting one file
    with session_scope() as db:
        freed = lc.purge(db, cid, _settings())
        assert freed == 2000 and db.get(Clip, cid).lifecycle == "purged"
    with session_scope() as db:
        assert lc.purge(db, cid, _settings()) == 0  # second run: nothing to do, no error


def test_disk_pressure_prioritises_lowest_scores(store, client):
    low, mid = _clip(store, "q1", score=5), _clip(store, "q2", score=45)
    with session_scope() as db:
        _authorize_all(db)
        p = lc.plan(db, _settings())
        order = [i["clip_id"] for i in sorted(p["eligible"], key=lambda i: (i["score"], i["created_at"]))]
        assert order.index(low) < order.index(mid)


def test_api_simulation_flags_and_retro_phrase(store, admin):
    cid = _clip(store, "api")
    sim = admin.get("/api/storage/simulation?include_existing=true").json()
    assert "eligible" in sim and "counts" in sim
    assert admin.post(f"/api/storage/clips/{cid}/protect", json={"value": True}, headers=CSRF).json()["protected"]
    assert admin.post("/api/storage/authorize-retroactive", json={"confirm": "sim"}, headers=CSRF).status_code == 422
    ov = admin.get("/api/storage/overview").json()
    assert {"bytes", "clips", "recovered_bytes", "enabled", "dry_run"} <= set(ov)
    assert ov["enabled"] is False and ov["dry_run"] is True  # safe defaults
