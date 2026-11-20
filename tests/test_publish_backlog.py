"""v0.7.3: publications stuck after a deferral, rate gap from real uploads, several videos per peak slot,
late publications spread over the next slots and the "repack" of the schedule."""

from __future__ import annotations

from collections import Counter
from datetime import timedelta

from sqlalchemy import select

from clipper import queue
from clipper.db import session_scope, utcnow
from clipper.models import Clip, Job, Publication, PublishAccount, PublishRule
from clipper.publish import editorial as ed, runner, service
from clipper.publish.base import PublishError
from conftest import CSRF
from test_editorial_ops import _SID, _setup


def _keys(db, pid):
    return {j.idempotency_key for j in db.scalars(select(Job).where(Job.type == "publish"))
            if j.payload.get("publication_id") == pid}


def _pub(db, clip_id, aid, when=None):
    return service.create_publication(db, db.get(Clip, clip_id), db.get(PublishAccount, aid), origin="manual",
                                      user="t@x", rights_confirmed=True, scheduled_at=when)


def test_a_deferred_publication_gets_a_new_queue_job(client):
    ids, aid = _setup("defer", n=1)
    with session_scope() as db:
        p = _pub(db, ids[0], aid)
        service.enqueue_due(db)
        first = _keys(db, p.id)
        runner._reschedule(p, "intervalo mínimo entre envios", 60)  # what the runner does on a rate gap
        assert p.attempts == 0 and p.status == "scheduled"
        p.scheduled_at = utcnow() - timedelta(seconds=1)  # the minute passed
        db.flush()
        service.enqueue_due(db)
        assert len(_keys(db, p.id) - first) == 1  # before v0.7.3 the same finished key: never sent again


def test_changing_the_time_by_hand_also_gets_a_new_job(admin):
    ids, aid = _setup("retime", n=1)
    with session_scope() as db:
        p = _pub(db, ids[0], aid)
        service.enqueue_due(db)
        pid, first = p.id, _keys(db, p.id)
    soon = (utcnow() + timedelta(minutes=5)).isoformat() + "Z"
    assert admin.patch(f"/api/publish/publications/{pid}", json={"scheduled_at": soon}, headers=CSRF).status_code == 200
    with session_scope() as db:
        p = db.get(Publication, pid)
        p.scheduled_at = utcnow() - timedelta(seconds=1)
        db.flush()  # sessions here do not autoflush
        service.enqueue_due(db)
        assert len(_keys(db, pid) - first) == 1


def test_a_past_time_explains_what_to_do(admin):
    ids, aid = _setup("past", n=1)
    with session_scope() as db:
        pid = _pub(db, ids[0], aid, utcnow() + timedelta(hours=3)).id
    r = admin.patch(f"/api/publish/publications/{pid}", json={"scheduled_at": (utcnow() - timedelta(hours=1)).isoformat() + "Z"},
                    headers=CSRF)
    assert r.status_code == 422 and "Enviar agora" in r.json()["detail"]["problems"][0]
    assert admin.patch(f"/api/publish/publications/{pid}", json={"scheduled_at": None}, headers=CSRF).status_code == 200


def _run(pid, monkeypatch):
    """Run the publish job of ``pid`` up to the token step (tokens fail -> no real upload)."""
    def no_tokens(db, acct):
        raise PublishError("sem rede no teste", retryable=True)

    monkeypatch.setattr(service, "tokens_for", no_tokens)
    with session_scope() as db:
        service.enqueue_due(db)
    with session_scope() as db:
        jobs = queue.claim(db, runner.worker_id(), ["publish"], has_gpu=False, limit=50, lease_seconds=600)
        jid = next(j.id for j in jobs if j.payload["publication_id"] == pid)
        for j in jobs:
            if j.id != jid:
                queue.complete(db, j.id, runner.worker_id(), {})
    runner.run_publish(jid, pid)
    with session_scope() as db:
        return db.get(Publication, pid).last_error


def test_status_polls_do_not_block_the_next_upload(monkeypatch):
    ids, aid = _setup("gap", n=3)
    with session_scope() as db:
        old = _pub(db, ids[0], aid)
        old.status, old.published_at = "processing", None  # a Reel processing: polled every minute
        old.updated_at = utcnow()
        pid = _pub(db, ids[1], aid).id
    assert "intervalo" not in _run(pid, monkeypatch)
    with session_scope() as db:
        busy = _pub(db, ids[2], aid)
        busy.status = "uploading"  # a real upload in progress on the same account
        p = db.get(Publication, pid)
        p.status, p.scheduled_at = "scheduled", utcnow() - timedelta(seconds=1)
    assert _run(pid, monkeypatch) == "intervalo mínimo entre envios"


def test_several_videos_share_a_peak_slot(client):
    ids, aid = _setup("perslot", n=1)
    with session_scope() as db:
        acct = db.get(PublishAccount, aid)
        rule = PublishRule(account_id=aid, mode="scheduled", slots=["12:00", "18:00"], max_per_day=10, per_slot=3,
                           timezone="America/Sao_Paulo")
        db.add(rule)
        db.flush()
        got = []
        for _ in range(4):
            s = service.next_slot(db, acct, rule)
            db.add(Publication(clip_id=None, account_id=aid, platform="youtube", status="scheduled", scheduled_at=s))
            db.flush()
            got.append(s)
        assert got[0] == got[1] == got[2] and got[3] > got[2]


def test_late_publications_are_spread_not_sent_in_a_burst(client):
    ids, aid = _setup("late", n=5)
    with session_scope() as db:
        db.add(PublishRule(account_id=aid, enabled=True, mode="scheduled", slots=["12:00", "18:00", "21:00"],
                           max_per_day=10, per_slot=2, timezone="America/Sao_Paulo"))
        db.flush()
        pids = [_pub(db, i, aid, utcnow() - timedelta(days=1)).id for i in ids]
        service.enqueue_due(db)
        pubs = [db.get(Publication, i) for i in pids]
        now = utcnow()
        assert all(p.scheduled_at > now for p in pubs)  # nothing queued in a burst
        assert max(Counter(p.scheduled_at for p in pubs).values()) <= 2
        assert all(any(e["event"] == "respread" for e in p.log) for p in pubs)
        assert not any(_keys(db, i) for i in pids)


def test_repack_previews_then_brings_the_schedule_forward(admin):
    ids, aid = _setup("repack", n=4)
    with session_scope() as db:
        rule = PublishRule(account_id=aid, enabled=True, mode="scheduled", slots=["12:00", "18:00", "21:00"],
                           max_per_day=12, per_slot=4, timezone="America/Sao_Paulo")
        db.add(rule)
        db.flush()
        far = [utcnow() + timedelta(days=d) for d in (5, 6, 7, 8)]
        pids = [_pub(db, i, aid, w).id for i, w in zip(ids, far)]
    prev = admin.post("/api/publish/repack", json={"account_id": aid, "dry_run": True}, headers=CSRF).json()
    assert len(prev["moved"]) == 4
    with session_scope() as db:
        assert [db.get(Publication, i).scheduled_at for i in pids] == far  # preview changes nothing
    done = admin.post("/api/publish/repack", json={"account_id": aid, "dry_run": False}, headers=CSRF).json()
    assert len(done["moved"]) == 4
    with session_scope() as db:
        times = [db.get(Publication, i).scheduled_at for i in pids]
        assert len(set(times)) == 1 and times[0] < far[0]  # all four in the next peak slot


def test_migration_0014_adds_per_slot_and_rolls_back(tmp_path):
    import sqlalchemy as sa

    from clipper.migrate import downgrade_db, upgrade_db

    url = f"sqlite:///{(tmp_path / 'r.db').as_posix()}"
    upgrade_db(url)
    eng = sa.create_engine(url)
    with eng.begin() as c:
        c.execute(sa.text("insert into publish_accounts (platform, external_id, name, handle, status, scopes, meta, quota, "
                          "last_error, connected_by, tokens_enc, connected_at) values ('youtube','x','X','x','connected','[]','{}','{}','','','','2026-11-20')"))
        c.execute(sa.text("insert into publish_rules (account_id, enabled, mode, require_approval, min_score, privacy, "
                          "allow_public_auto, max_per_day, slots, timezone, templates, created_at, updated_at) values "
                          "(1, 1, 'scheduled', 1, 70, 'private', 0, 5, '[\"12:00\"]', 'America/Sao_Paulo', '{}', '2026-11-20', '2026-11-20')"))
        assert c.execute(sa.text("select per_slot from publish_rules")).scalar() == 1  # old behaviour by default
    downgrade_db(url, "0013")
    with eng.connect() as c:
        assert "per_slot" not in {x["name"] for x in sa.inspect(c).get_columns("publish_rules")}
        assert c.execute(sa.text("select max_per_day from publish_rules")).scalar() == 5  # rule kept
    upgrade_db(url)


def test_autofill_previews_then_fills_late_failed_and_never_scheduled(admin):
    ids, aid = _setup("fill", n=4)
    with session_scope() as db:
        db.add(PublishRule(account_id=aid, enabled=True, mode="scheduled", slots=["12:00", "18:00", "21:00"],
                           max_per_day=12, per_slot=4, min_score=0, privacy="private", timezone="America/Sao_Paulo",
                           streamer_id=_SID[aid]))
        for i in ids:
            db.get(Clip, i).created_at = utcnow() - timedelta(days=10)  # older than the 48 h automatic scan
        late = _pub(db, ids[0], aid, utcnow() - timedelta(days=1))
        bad = _pub(db, ids[1], aid, utcnow() - timedelta(days=1))
        bad.status, bad.last_error = "failed", "HTTP 500"
        bad.remote_status = {"failure": "before_publish"}
        future = _pub(db, ids[3], aid, utcnow() + timedelta(days=9))  # moved by hand: must keep its time
        late_id, bad_id, future_id, future_at = late.id, bad.id, future.id, future.scheduled_at
    prev = admin.post("/api/publish/autofill", json={"account_id": aid, "dry_run": True}, headers=CSRF).json()
    assert [m["id"] for m in prev["moved"]] == [late_id] and [m["id"] for m in prev["retried"]] == [bad_id]
    assert [m["clip_id"] for m in prev["new"]] == [ids[2]]
    with session_scope() as db:
        assert db.get(Publication, bad_id).status == "failed"  # preview changes nothing
        assert db.scalar(select(Publication.id).where(Publication.clip_id == ids[2])) is None
    done = admin.post("/api/publish/autofill", json={"account_id": aid, "dry_run": False}, headers=CSRF).json()
    assert len(done["new"]) == 1 and len(done["retried"]) == 1
    with session_scope() as db:
        pubs = list(db.scalars(select(Publication).where(Publication.account_id == aid)))
        assert len(pubs) == 4 and {p.status for p in pubs} == {"scheduled"}
        assert all(p.scheduled_at > utcnow() for p in pubs)
        assert db.get(Publication, future_id).scheduled_at == future_at


def test_dragging_a_failed_post_back_onto_the_calendar(admin):
    ids, aid = _setup("drag", n=2)
    with session_scope() as db:
        ok = _pub(db, ids[0], aid)
        ok.status, ok.remote_status = "failed", {"failure": "before_publish"}
        risky = _pub(db, ids[1], aid)
        risky.status, risky.upload_state = "failed", {"publish_called_at": "2026-11-20T10:00:00Z"}
        ok_id, risky_id = ok.id, risky.id
    when = (utcnow() + timedelta(days=1)).isoformat() + "Z"
    r = admin.patch(f"/api/publish/publications/{ok_id}", json={"scheduled_at": when}, headers=CSRF)
    assert r.status_code == 200 and r.json()["status"] == "scheduled"
    r = admin.patch(f"/api/publish/publications/{risky_id}", json={"scheduled_at": when}, headers=CSRF)
    assert r.status_code == 422 and "confira no perfil" in r.json()["detail"]["problems"][0]
