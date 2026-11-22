"""Customer daily allowance on a REAL PostgreSQL: many workers (separate connections) race for the last slots.

Runs only with ``ASC_TEST_PG_URL`` (disposable server: infrastructure/compose/test-postgres.yml or the CI service);
refuses 192.168.1.212 and any non-local host. NOT executed until such a server is available.
"""

from __future__ import annotations

import uuid

import pytest
import sqlalchemy as sa

from test_postgres_shared_lives import _customer, _parallel, _run, pg  # noqa: F401

pytestmark = pytest.mark.postgres


def test_concurrent_workers_never_exceed_the_daily_total(pg):
    from clipper import customer_publish
    from clipper.db import utcnow
    from clipper.models import (Clip, CustomerPublishSettings, Publication, PublishAccount, PublishRule, PublishUnit,
                                StreamSession, Streamer)

    engine, make = pg
    uid = _customer(make, "creator")  # 3 per day in total

    def setup(db):
        s = Streamer(platform="twitch", channel=uuid.uuid4().hex[:10], url="https://www.twitch.tv/x", hts_monitoring=False)
        db.add(s)
        db.flush()
        sess = StreamSession(streamer_id=s.id, status="ended")
        db.add(sess)
        db.flush()
        db.add(CustomerPublishSettings(user_id=uid, enabled=True, terms_accepted_at=utcnow()))
        accts = [PublishAccount(platform="youtube", external_id=uuid.uuid4().hex, owner_id=uid, status="connected")
                 for _ in range(2)]
        db.add_all(accts)
        db.flush()
        rule = PublishRule(owner_id=uid, account_id=accts[0].id, streamer_id=s.id, rights_confirmed_at=utcnow())
        db.add(rule)
        db.flush()
        ids = []
        for i in range(10):
            clip = Clip(session_id=sess.id, streamer_id=s.id, start=i * 40, end=i * 40 + 30, peak=1, score=90, status="ready")
            db.add(clip)
            db.flush()
            pub = Publication(clip_id=clip.id, account_id=accts[i % 2].id, rule_id=rule.id, platform="youtube",
                              owner_id=uid, status="scheduled", scheduled_at=utcnow())
            db.add(pub)
            db.flush()
            ids.append(pub.id)
        return ids
    ids = _run(make, setup)

    def reserve(i):
        return _run(make, lambda db: customer_publish.reserve(db, db.get(Publication, ids[i])))
    results = _parallel(10, reserve)
    assert results.count("") == 3, results
    # A retry of a reserved publication never takes a second unit
    _parallel(5, lambda i: _run(make, lambda db: customer_publish.reserve(db, db.get(Publication, ids[results.index("")]))))
    with make() as db:
        held = db.scalar(sa.select(sa.func.count()).select_from(PublishUnit).where(
            PublishUnit.user_id == uid, PublishUnit.state.in_(("reserved", "consumed"))))
        assert held == 3


def test_cleaner_and_worker_race_on_real_connections(pg):
    """The abandoned-reservation cleaner and a worker re-reserving the same publication, on separate connections:
    the worker always keeps its reservation (the cleaner re-reads under the account lock)."""
    from datetime import timedelta

    from clipper import customer_publish
    from clipper.db import utcnow
    from clipper.models import (Clip, CustomerPublishSettings, Publication, PublishAccount, PublishRule, PublishUnit,
                                StreamSession, Streamer)

    engine, make = pg
    uid = _customer(make, "pro")

    def setup(db):
        s = Streamer(platform="twitch", channel=uuid.uuid4().hex[:10], url="https://www.twitch.tv/y", hts_monitoring=False)
        db.add(s)
        db.flush()
        sess = StreamSession(streamer_id=s.id, status="ended")
        db.add(sess)
        db.flush()
        db.add(CustomerPublishSettings(user_id=uid, enabled=True, terms_accepted_at=utcnow()))
        acct = PublishAccount(platform="youtube", external_id=uuid.uuid4().hex, owner_id=uid, status="connected")
        db.add(acct)
        db.flush()
        rule = PublishRule(owner_id=uid, account_id=acct.id, streamer_id=s.id, rights_confirmed_at=utcnow())
        db.add(rule)
        clip = Clip(session_id=sess.id, streamer_id=s.id, start=0, end=30, peak=1, score=90, status="ready")
        db.add(clip)
        db.flush()
        pub = Publication(clip_id=clip.id, account_id=acct.id, rule_id=rule.id, platform="youtube", owner_id=uid,
                          status="scheduled", scheduled_at=utcnow())
        db.add(pub)
        db.flush()
        return pub.id
    pid = _run(make, setup)
    for _ in range(10):
        def reset(db):
            old = utcnow() - timedelta(hours=10)
            u = db.scalar(sa.select(PublishUnit).where(PublishUnit.publication_id == pid))
            if u is None:
                db.add(PublishUnit(user_id=uid, publication_id=pid, day="2026-01-01", state="released", created_at=old, reserved_at=old))
            else:
                u.state, u.reserved_at = "released", old
        _run(make, reset)
        _parallel(2, lambda i: _run(make, (lambda db: customer_publish.reserve(db, db.get(Publication, pid))) if i == 0
                                    else customer_publish.release_abandoned))
        with make() as db:
            assert db.scalar(sa.select(PublishUnit.state).where(PublishUnit.publication_id == pid)) == "reserved"
