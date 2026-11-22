"""Shared lives on a REAL PostgreSQL, with real concurrent connections (row locks, partial unique indexes).

Runs only when ``ASC_TEST_PG_URL`` points to a disposable test server, e.g. the container in
``infrastructure/compose/test-postgres.yml`` or the ``postgres`` service of the CI job:

    ASC_TEST_PG_URL=postgresql+psycopg://asc_test:asc_test@127.0.0.1:55432/postgres

Each run creates its own database ``asc_test_<random>`` there, migrates it from scratch with Alembic and drops
it at the end. Safety: refuses any host other than localhost / 127.0.0.1 / ``postgres`` (CI service) and the
production address 192.168.1.212, whatever the port.
"""

from __future__ import annotations

import os
import threading
import time
import uuid
from datetime import timezone
from urllib.parse import urlsplit

import pytest
import sqlalchemy as sa
from sqlalchemy.orm import sessionmaker

pytestmark = pytest.mark.postgres

ALLOWED_HOSTS = {"localhost", "127.0.0.1", "postgres"}
DAY = 86_400
# Same limits as the commercial catalog (clipper.catalog.limits_for, checked by test_plan_limits_unchanged):
# publishing Basic none, Creator 2 destinations / 3 per day in total, Pro 4 / 4.
LIMITS = {
    "basic": {"hours": 40, "channels": 1, "simultaneous": 1, "storage_gb": 5, "export_4k": 0,
              "features": {"ai": "economy", "secondPass": False, "variants": False, "priority": False, "editing": False,
                           "autoPublish": False, "publishDestinations": 0, "publishDaily": 0}},
    "creator": {"hours": 100, "channels": 2, "simultaneous": 2, "storage_gb": 12, "export_4k": 0,
                "features": {"ai": "enhanced", "secondPass": False, "variants": False, "priority": False, "editing": True,
                             "autoPublish": True, "publishDestinations": 2, "publishDaily": 3}},
    "pro": {"hours": 200, "channels": 4, "simultaneous": 2, "storage_gb": 25, "export_4k": 30,
            "features": {"ai": "premium", "secondPass": True, "variants": True, "priority": True, "editing": True,
                         "autoPublish": True, "publishDestinations": 4, "publishDaily": 4}},
}


def _safe_admin_url() -> str:
    """Admin URL of the throwaway server (tests/pg_support.py). Without one: skipped, or FAILED under
    ASC_REQUIRE_PG=1 (homologation never reports PostgreSQL tests as skipped)."""
    import pg_support

    try:
        url = pg_support.admin_url()
    except pg_support.Refused as exc:
        pytest.fail(str(exc))
    if url is None:
        if pg_support.required():
            pytest.fail("ASC_REQUIRE_PG=1 but ASC_TEST_PG_URL is not set: start the throwaway PostgreSQL")
        pytest.skip("ASC_TEST_PG_URL not set: PostgreSQL homologation tests need a disposable test server")
    return url


@pytest.fixture(scope="module")
def pg():
    from clipper.migrate import upgrade_db

    admin_url = _safe_admin_url()
    name = f"asc_test_{uuid.uuid4().hex[:10]}"
    admin = sa.create_engine(admin_url, isolation_level="AUTOCOMMIT")
    with admin.connect() as c:
        c.execute(sa.text(f'CREATE DATABASE "{name}"'))
    url = admin_url.rsplit("/", 1)[0] + f"/{name}"
    upgrade_db(url)
    engine = sa.create_engine(url, pool_size=30, max_overflow=10)
    yield engine, sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    engine.dispose()
    with admin.connect() as c:
        c.execute(sa.text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
    admin.dispose()


def _run(make, fn, retries: int = 5):
    """One unit of work in its own connection/transaction, retried like a worker request would be."""
    from sqlalchemy.exc import DBAPIError

    for attempt in range(retries):
        db = make()
        try:
            out = fn(db)
            db.commit()
            return out
        except DBAPIError:
            db.rollback()
            if attempt == retries - 1:
                raise
            time.sleep(0.05 * (attempt + 1))
        finally:
            db.close()


def _parallel(n: int, target) -> list:
    results, errors = [None] * n, []
    barrier = threading.Barrier(n)

    def work(i):
        try:
            barrier.wait()
            results[i] = target(i)
        except Exception as exc:  # pragma: no cover - reported below
            errors.append(exc)
    threads = [threading.Thread(target=work, args=(i,)) for i in range(n)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert not errors, errors
    return results


def _customer(make, plan: str, *, hours=None, status="active", financial=None) -> int:
    from clipper.models import User

    def fn(db):
        u = User(email=f"c-{uuid.uuid4().hex[:10]}@example.com", password_hash="", role="customer", active=True)
        db.add(u)
        db.flush()
        _snapshot(db, u.id, plan, seq=1, hours=hours, status=status, financial=financial)
        return u.id
    return _run(make, fn)


def _snapshot(db, uid: int, plan: str, *, seq: int, hours=None, status="active", financial=None, until_days=30):
    from clipper import entitlements
    from clipper.db import utcnow

    now = int(utcnow().replace(tzinfo=timezone.utc).timestamp())
    limits = {**LIMITS[plan], **({"hours": hours} if hours is not None else {})}
    return entitlements.apply_snapshot(db, {
        "event_id": uuid.uuid4().hex, "seq": seq, "provider": "stripe", "subscription_ref": f"sub_{uid}", "account_id": uid,
        "plan": plan, "market": "US", "financial_status": financial or status, "review": "",
        "access": {"status": status, "until": now + until_days * DAY if until_days > 0 else now - 1, "period_start": now - DAY,
                   "period_end": now + 29 * DAY, "renews_at": None, "cancel_at_period_end": False},
        "limits": limits, "manage": "stripe_portal", "renewal": None, "payments": []})


def _channel(make, *, hts=False) -> int:
    from clipper.models import Streamer

    def fn(db):
        name = uuid.uuid4().hex[:10]
        s = Streamer(platform="twitch", channel=name, url=f"https://www.twitch.tv/{name}", hts_monitoring=hts)
        db.add(s)
        db.flush()
        return s.id
    return _run(make, fn)


def _follow(make, uid: int, sid: int, preset="balanced") -> int:
    from clipper.models import ChannelMonitor

    def fn(db):
        m = ChannelMonitor(user_id=uid, streamer_id=sid, preset=preset)
        db.add(m)
        db.flush()
        return m.id
    return _run(make, fn)


def _segment(make, sess_id: int, idx: int, start: float, duration: float, run_id: int = 0):
    from clipper import pipeline

    return _run(make, lambda db: pipeline.register_segments(
        db, sess_id, f"w{run_id}", "http://seg", [{"idx": idx, "start": start, "duration": duration, "run_id": run_id}]))


def test_ten_customers_concurrent_connections_one_capture_one_analysis(pg):
    from clipper import entitlements, pipeline
    from clipper.models import Job, MonitorSession, Segment, StreamSession, UsageEntry

    engine, make = pg
    sid = _channel(make)
    plans = ["basic"] * 4 + ["creator"] * 3 + ["pro"] * 3
    uids = [_customer(make, plan, hours=1 if i == 0 else None) for i, plan in enumerate(plans)]
    for uid in uids:
        _follow(make, uid, sid)

    # 20 concurrent "live" reports from separate connections: one session, one capture job
    _parallel(20, lambda i: _run(make, lambda db: pipeline.report_status(db, sid, live=True, title="live")))
    with make() as db:
        sessions = db.scalars(sa.select(StreamSession).where(StreamSession.streamer_id == sid)).all()
        assert len(sessions) == 1 and sessions[0].status == "live"
        sess_id = sessions[0].id
        captures = [j for j in db.scalars(sa.select(Job).where(Job.type == "capture")) if j.payload.get("session_id") == sess_id]
        assert len(captures) == 1
        assert len(entitlements.attachments(db, sess_id, "watching")) == 10

    # Two capture workers post different segments concurrently (plus a reconnect re-post): no deadlock,
    # one base analysis job per segment, every customer charged exactly once per followed segment
    _parallel(3, lambda i: _segment(make, sess_id, i, i * 1200.0, 1200.0))
    _segment(make, sess_id, 1, 1200.0, 1200.0, run_id=1)  # reconnect
    with make() as db:
        assert db.scalar(sa.select(sa.func.count()).select_from(Segment).where(Segment.session_id == sess_id)) == 3
        audio = [j for j in db.scalars(sa.select(Job).where(Job.type == "audio_analyze")) if j.payload.get("session_id") == sess_id]
        assert len(audio) == 3
        profile = db.get(StreamSession, sess_id).base_profile
        assert all(j.payload["whisper_model"] == profile["whisper_model"] for j in audio)
        rows = db.scalars(sa.select(UsageEntry).where(UsageEntry.source_key.like(f"live:%:{sess_id}:%"))).all()
        assert len(rows) == 30
        assert {r.user_id: 0 for r in rows}.keys() == set(uids)
        first = db.scalar(sa.select(MonitorSession).where(MonitorSession.user_id == uids[0], MonitorSession.session_id == sess_id))
        assert first.state == "stopped" and "franquia" in first.stop_reason  # 1-hour franchise, alone
        assert len(entitlements.attachments(db, sess_id, "watching")) == 9

    # Suspension and cancellation of one customer each, concurrently with the next segment: only they stop
    def suspend(db):
        _snapshot(db, uids[1], "basic", seq=2, status="suspended")

    def cancel(db):
        _snapshot(db, uids[4], "creator", seq=2, status="ended", financial="canceled", until_days=0)
    _parallel(3, lambda i: _run(make, [suspend, cancel, lambda db: pipeline.register_segments(
        db, sess_id, "w1", "http://seg", [{"idx": 3, "start": 3600.0, "duration": 600.0}])][i]))
    with make() as db:
        watching = {a.user_id for a in entitlements.attachments(db, sess_id, "watching")}
        assert uids[1] not in watching and uids[4] not in watching and len(watching) == 7
        assert pipeline.session_state(db, sess_id)["stop"] is False


def test_concurrent_delivery_is_deduplicated_and_the_shared_file_is_protected(pg):
    from clipper import pipeline, sharing, storage_lifecycle
    from clipper.models import Clip, ClipShare

    engine, make = pg
    sid = _channel(make)
    uids = [_customer(make, "pro") for _ in range(10)]
    for uid in uids:
        _follow(make, uid, sid)
    sess_id = _run(make, lambda db: pipeline.report_status(db, sid, live=True)["session_id"])
    _segment(make, sess_id, 0, 0.0, 600.0)

    def make_clip(db):
        c = Clip(session_id=sess_id, streamer_id=sid, start=100, end=130, peak=110, score=80, status="ready",
                 video_path=f"clips/{uuid.uuid4().hex}/v1.mp4", file_size=2_000_000)
        db.add(c)
        db.flush()
        return c.id
    cid = _run(make, make_clip)
    # Eight workers announce the same ready clip at once: each customer gets exactly one share
    _parallel(8, lambda i: _run(make, lambda db: sharing.on_clip_status(db, db.get(Clip, cid))))
    with make() as db:
        shares = db.scalars(sa.select(ClipShare).where(ClipShare.clip_id == cid, ClipShare.revoked_at.is_(None))).all()
        assert sorted(s.user_id for s in shares) == sorted(uids)
        assert len({s.video_path for s in shares}) == 1, "one physical file"
        assert any("compartilhado" in why for why in storage_lifecycle.protection(db, db.get(Clip, cid)))


def test_capture_worker_failure_starts_a_new_session_without_duplicates(pg):
    from clipper import pipeline
    from clipper.models import Job, StreamSession

    engine, make = pg
    sid = _channel(make, hts=True)
    uid = _customer(make, "creator")
    _follow(make, uid, sid)
    first = _run(make, lambda db: pipeline.report_status(db, sid, live=True)["session_id"])

    def kill(db):
        job = pipeline.capture_job_for(db, first)
        job.status = "dead"
    _run(make, kill)
    # The channel is still live: concurrent reports replace the dead capture with ONE new session
    _parallel(10, lambda i: _run(make, lambda db: pipeline.report_status(db, sid, live=True)))
    with make() as db:
        rows = db.scalars(sa.select(StreamSession).where(StreamSession.streamer_id == sid).order_by(StreamSession.id)).all()
        assert [r.status for r in rows] == ["failed", "live"]
        live = rows[1].id
        caps = [j for j in db.scalars(sa.select(Job).where(Job.type == "capture")) if j.payload.get("streamer_id") == sid]
        assert sorted(j.payload["session_id"] for j in caps) == [first, live]


def test_simultaneous_lives_limit_holds_across_connections(pg):
    from clipper import entitlements, pipeline

    engine, make = pg
    uid = _customer(make, "basic")  # 1 simultaneous live
    channels = [_channel(make, hts=True) for _ in range(2)]
    for sid in channels:
        _follow(make, uid, sid)  # the second monitor bypasses the channel limit on purpose: only lives are tested
    # Both channels go live at the same moment, from separate connections: the customer follows only one
    _parallel(2, lambda i: _run(make, lambda db: pipeline.report_status(db, channels[i], live=True)))
    with make() as db:
        assert entitlements.live_sessions(db, uid) == 1


def test_hts_channels_and_isolation_unchanged(pg):
    from clipper import pipeline
    from clipper.models import Segment

    engine, make = pg
    sid = _channel(make, hts=True)
    sess_id = _run(make, lambda db: pipeline.report_status(db, sid, live=True)["session_id"])
    _segment(make, sess_id, 0, 0.0, 500 * 3600.0)
    with make() as db:
        assert db.scalar(sa.select(Segment.analysis_state).where(Segment.session_id == sess_id)) != "skipped"
        assert pipeline.session_state(db, sess_id)["stop"] is False
