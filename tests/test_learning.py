"""Phase 5: continuous learning from the owner's decisions."""

from __future__ import annotations

from sqlalchemy import select

from clipper.db import session_scope, utcnow
from clipper.evaluation import feedback
from clipper.models import AuditLog, Clip, EvalExample, LearningRun, RankerModel, Review, Streamer, StreamSession
from conftest import CSRF


def _clip(tag: str, score=70.0, status="ready", kinds=("laughter",)) -> int:
    with session_scope() as db:
        s = Streamer(platform="twitch", channel=f"lr_{tag}", url=f"https://www.twitch.tv/lr_{tag}", display_name=tag)
        db.add(s)
        db.flush()
        sess = StreamSession(streamer_id=s.id, status="ended")
        db.add(sess)
        db.flush()
        c = Clip(session_id=sess.id, streamer_id=s.id, start=10, end=40, peak=20, score=score, status=status,
                 kinds=list(kinds), score_breakdown={"intensity": 6.0, "reaction": 7.0, "humor": 5.0},
                 source_path="clips/x/source.mp4", source_start=0, source_duration=60)
        db.add(c)
        db.flush()
        return c.id


def _label(db, cid):
    ex = db.scalar(select(EvalExample).where(EvalExample.clip_id == cid))
    return db.scalar(select(Review).where(Review.example_id == ex.id)) if ex else None


def test_approve_and_reject_in_the_clip_page_become_human_labels(admin):
    a, r = _clip("appr"), _clip("rej")
    assert admin.post(f"/api/clips/{a}/approve", headers=CSRF).status_code == 200
    assert admin.post(f"/api/clips/{r}/reject", headers=CSRF).status_code == 200
    with session_scope() as db:
        la, lr = _label(db, a), _label(db, r)
        assert la.rating == "good" and la.source == "clip_action"
        assert lr.rating == "bad" and lr.source == "clip_action"


def test_explicit_review_is_never_overwritten_by_approve(admin):
    cid = _clip("explicit")
    ex_id = admin.post(f"/api/review/clips/{cid}/example", headers=CSRF).json()["id"]
    with session_scope() as db:
        db.get(EvalExample, ex_id).media_state = "ready"
    admin.put(f"/api/review/examples/{ex_id}/review", json={"rating": "excellent", "category": "crash"}, headers=CSRF)
    admin.post(f"/api/clips/{cid}/reject", headers=CSRF)
    with session_scope() as db:
        rv = _label(db, cid)
        assert rv.rating == "excellent" and rv.source == "review"


def test_automatic_rejection_by_the_director_is_not_a_label(client):
    cid = _clip("auto", status="rejected")  # status set by the system, no human action
    with session_scope() as db:
        assert _label(db, cid) is None


def test_time_edit_is_kept_as_ideal_bounds(admin):
    cid = _clip("edit")
    assert admin.patch(f"/api/clips/{cid}", json={"start": 5.0, "end": 38.0}, headers=CSRF).status_code == 200
    admin.post(f"/api/clips/{cid}/approve", headers=CSRF)
    with session_scope() as db:
        rv = _label(db, cid)
        assert (rv.short_start, rv.short_end) == (5.0, 38.0)
        assert db.get(Clip, cid).provenance["human_edit"]["ai_start"] == 10


def test_backfill_from_audit_uses_the_last_human_action(client):
    cid = _clip("hist")
    with session_scope() as db:
        db.add(AuditLog(user_email="admin@asc.local", action="clip.approve", target=f"clip:{cid}"))
        db.add(AuditLog(user_email="admin@asc.local", action="clip.reject", target=f"clip:{cid}"))
        db.add(AuditLog(user_email="admin@asc.local", action="clip.approve", target="clip:999999"))  # deleted
    with session_scope() as db:
        out = feedback.backfill_from_audit(db)
        assert out["clips_deleted_before"] >= 1
    with session_scope() as db:
        assert _label(db, cid).rating == "bad"


def test_profile_shows_patterns_early_but_flags_small_samples(client):
    with session_scope() as db:
        p = feedback.profile(db)
    assert p["labels"] >= 1 and ("insuficientes" in p["note"]) == (p["labels"] < 30)
    assert all({"n", "rate", "ci95", "reliable"} <= set(g) for g in p["by_signal"])
    assert all(not g["reliable"] for g in p["by_signal"] if g["n"] < feedback.INSUFFICIENT)


def test_learning_job_without_enough_data_only_updates_the_profile(client):
    with session_scope() as db:
        for m in db.scalars(select(RankerModel)):
            m.status = "archived"
    with session_scope() as db:
        run = feedback.learn(db)
        db.flush()
        assert run.outcome in ("profile_only", "trained_shadow", "promoted")
        if run.outcome == "profile_only":
            assert run.profile["labels"] >= 1
    with session_scope() as db:
        st = feedback.status(db)
        assert st["last_run"] is not None and st["human_labels"] >= 1
        assert db.scalar(select(LearningRun).order_by(LearningRun.id.desc())) is not None


def test_learning_status_endpoint(admin):
    r = admin.get("/api/review/learning")
    assert r.status_code == 200
    body = r.json()
    assert {"status", "profile"} <= set(body) and "active_model" in body["status"]
