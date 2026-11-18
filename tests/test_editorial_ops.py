"""Phase 5 stage F: batch approval, batch scheduling, semi-automatic proposals, emergency pause,
calendar and reports."""

from __future__ import annotations

from datetime import timedelta

from sqlalchemy import select

from clipper.db import session_scope, utcnow
from clipper.models import Clip, Job, Publication, PublishAccount, PublishRule, Streamer, StreamSession
from clipper.publish import crypto, editorial as ed, runner, service, youtube
from clipper.publish.base import Tokens
from conftest import CSRF


def _setup(tag: str, n: int = 3, *, status="approved", rights=True) -> tuple[list[int], int]:
    with session_scope() as db:
        s = Streamer(platform="twitch", channel=f"ef_{tag}", url=f"https://www.twitch.tv/ef_{tag}", display_name=tag,
                     preferences={"republish_allowed": rights})
        db.add(s)
        db.flush()
        sess = StreamSession(streamer_id=s.id, status="ended")
        db.add(sess)
        db.flush()
        ids = []
        for i in range(n):
            c = Clip(session_id=sess.id, streamer_id=s.id, start=i * 200.0, end=i * 200.0 + 30, peak=i * 200.0 + 10,
                     score=60 + i * 10, status=status, title=f"Corte {i}", video_path="clips/x/v.mp4",
                     analysis_meta={"vision_provider": "x"}, vision_state="done")
            db.add(c)
            db.flush()
            ids.append(c.id)
        a = PublishAccount(platform="youtube", external_id=f"ef_{tag}", name=f"Canal {tag}", status="connected",
                           tokens_enc=crypto.encrypt(Tokens("AT", "RT", utcnow() + timedelta(hours=1)).as_dict()),
                           meta={"privacy_options": list(youtube.PRIVACY)})
        db.add(a)
        db.flush()
        _SID[a.id] = s.id
        return ids, a.id


_SID: dict[int, int] = {}


def test_batch_approve_and_reject_record_human_labels(admin):
    ids, _ = _setup("bap", status="ready")
    r = admin.post("/api/clips/batch", json={"ids": ids + [999999], "action": "approve"}, headers=CSRF).json()
    assert sorted(r["done"]) == sorted(ids) and "999999" in {str(k) for k in r["skipped"]}
    with session_scope() as db:
        assert {db.get(Clip, i).status for i in ids} == {"approved"}
        from clipper.models import EvalExample, Review

        n = db.query(Review).join(EvalExample, EvalExample.id == Review.example_id).filter(
            EvalExample.clip_id.in_(ids), Review.source == "clip_action").count()
        assert n == len(ids)
    r = admin.post("/api/clips/batch", json={"ids": ids[:1], "action": "reject"}, headers=CSRF).json()
    assert r["done"] == ids[:1]


def test_batch_schedule_fills_the_next_slots_best_first(admin):
    ids, aid = _setup("bsch")
    r = admin.post("/api/publish/batch", json={"clip_ids": ids, "account_id": aid, "privacy": "private",
                                                "rights_confirmed": True}, headers=CSRF).json()
    assert len(r["scheduled"]) == 3 and not r["refused"]
    assert r["scheduled"][0]["clip_id"] == ids[-1]  # highest score gets the earliest slot
    times = [x["scheduled_at"] for x in r["scheduled"]]
    assert times == sorted(times) and len(set(times)) == 3
    with session_scope() as db:
        assert {p.privacy for p in db.scalars(select(Publication).where(Publication.account_id == aid))} == {"private"}


def test_semi_automatic_rule_only_proposes_until_a_human_confirms(admin, client):
    ids, aid = _setup("semi")
    with session_scope() as db:
        db.add(PublishRule(account_id=aid, streamer_id=_SID[aid], mode="semi", min_score=0, require_approval=False, privacy="public",
                           allow_public_auto=False, max_per_day=5, slots=["09:00", "13:00", "19:00"]))
    with session_scope() as db:
        assert service.auto_scan(db) >= 3
        pubs = list(db.scalars(select(Publication).where(Publication.account_id == aid)))
        assert {p.status for p in pubs} == {"proposed"} and {p.origin for p in pubs} == {"semi"}
        assert service.enqueue_due(db) == 0 or not any(
            j.payload.get("publication_id") in {p.id for p in pubs} for j in db.scalars(select(Job)))
        pid_ok, pid_no = pubs[0].id, pubs[1].id
    # public + semi is allowed because a human confirms each one (public AUTO stays forbidden)
    r = admin.post("/api/publish/proposals/confirm", json={"ids": [pid_ok]}, headers=CSRF).json()
    assert r["confirmed"] == [pid_ok]
    assert admin.post("/api/publish/proposals/dismiss", json={"ids": [pid_no]}, headers=CSRF).json()["dismissed"] == [pid_no]
    with session_scope() as db:
        assert db.get(Publication, pid_ok).status == "scheduled" and db.get(Publication, pid_no).status == "cancelled"
        assert service.auto_scan(db) == 0  # a dismissed proposal is not proposed again


def test_unconfirmed_proposals_expire_and_can_be_proposed_again(client):
    ids, aid = _setup("exp", n=1)
    with session_scope() as db:
        db.add(PublishRule(account_id=aid, streamer_id=_SID[aid], mode="semi", min_score=0, require_approval=False, privacy="private",
                           max_per_day=5, slots=["09:00"]))
    with session_scope() as db:
        assert service.auto_scan(db) == 1
        p = db.scalar(select(Publication).where(Publication.account_id == aid))
        p.scheduled_at = utcnow() - timedelta(minutes=1)
    with session_scope() as db:
        assert ed.expire_proposals(db) == 1
        assert db.scalar(select(Publication).where(Publication.account_id == aid)).status == "expired"
        assert service.auto_scan(db) == 1  # back in the calendar at the next slot
        assert db.scalar(select(Publication).where(Publication.account_id == aid)).status == "proposed"


def test_emergency_pause_stops_queueing_and_uploads(admin, client, monkeypatch):
    ids, aid = _setup("pause", n=1)
    assert admin.post("/api/publish/control", json={"paused": True, "reason": "teste"}, headers=CSRF).json()["paused"]
    try:
        with session_scope() as db:
            p = service.create_publication(db, db.get(Clip, ids[0]), db.get(PublishAccount, aid), origin="manual",
                                           user="t@x", rights_confirmed=True)
            pid = p.id
            assert service.enqueue_due(db) == 0 and service.auto_scan(db) == 0
        ran = []
        monkeypatch.setattr(runner, "run_publish", lambda j, p: ran.append(p))
        monkeypatch.setattr(runner, "poll_processing", lambda: None)
        monkeypatch.setattr(runner, "refresh_metrics", lambda: None)
        monkeypatch.setattr(runner, "run_learning", lambda: None)
        runner.tick({"scan": 1e12, "poll": 1e12, "metrics": 1e12})
        assert ran == []
    finally:
        admin.post("/api/publish/control", json={"paused": False}, headers=CSRF)
    with session_scope() as db:
        assert service.enqueue_due(db) >= 1
        assert db.get(Publication, pid).status == "scheduled"


def test_admin_retry_gets_a_new_queue_job(client):
    ids, aid = _setup("retry", n=1)
    with session_scope() as db:
        p = service.create_publication(db, db.get(Clip, ids[0]), db.get(PublishAccount, aid), origin="manual",
                                       user="t@x", rights_confirmed=True)
        service.enqueue_due(db)
        first = {j.idempotency_key for j in db.scalars(select(Job).where(Job.type == "publish"))}
        p.status = "failed"
        service.log_event(p, "retry_admin", "t")
        p.status, p.attempts = "scheduled", 0
        service.enqueue_due(db)
        keys = {j.idempotency_key for j in db.scalars(select(Job).where(Job.type == "publish"))}
        assert len(keys - first) == 1  # before v0.5.6 the retry reused the finished job's key


def test_calendar_reschedule_and_report(admin):
    ids, aid = _setup("cal", n=2)
    admin.post("/api/publish/batch", json={"clip_ids": ids, "account_id": aid, "rights_confirmed": True}, headers=CSRF)
    cal = admin.get("/api/publish/calendar?days=30").json()
    mine = [x for x in cal["items"] if x["clip_id"] in ids]
    assert len(mine) == 2 and all(x["when"] and "-03:00" in x["when"] for x in mine)
    later = (utcnow() + timedelta(days=3)).isoformat() + "Z"
    r = admin.patch(f"/api/publish/publications/{mine[0]['id']}", json={"scheduled_at": later}, headers=CSRF)
    assert r.status_code == 200, r.text
    past = (utcnow() - timedelta(days=1)).isoformat() + "Z"
    assert admin.patch(f"/api/publish/publications/{mine[0]['id']}", json={"scheduled_at": past},
                       headers=CSRF).status_code == 422
    with session_scope() as db:
        p = db.get(Publication, mine[1]["id"])
        p.status, p.published_at, p.metrics = "published", utcnow(), {"viewCount": 120, "likeCount": 9}
    rep = admin.get("/api/publish/report?days=30").json()
    assert rep["platforms"]["youtube"]["views"] >= 120 and rep["top"][0]["views"] >= 120
    assert rep["hours"] and rep["categories"]
