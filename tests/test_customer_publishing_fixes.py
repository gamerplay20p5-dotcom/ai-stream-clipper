"""PR #25 review fixes: deactivated accounts, reservation lifecycle, 7-day retention, removed channels (with races)."""

from __future__ import annotations

import threading
from datetime import timedelta

from sqlalchemy import select

from conftest import CSRF
from test_billing_entitlements import billing_env  # noqa: F401
from test_customer_publishing import (channel, connect, enable, fake, live_clip, make_due, pubs, run,  # noqa: F401
                                      subscriber, tick, units)


def _set(pid, **fields):
    from clipper.db import session_scope
    from clipper.models import Publication

    with session_scope() as db:
        p = db.get(Publication, pid)
        for k, v in fields.items():
            setattr(p, k, v)


def _unit(pid):
    from clipper.db import session_scope
    from clipper.models import PublishUnit

    with session_scope() as db:
        u = db.scalar(select(PublishUnit).where(PublishUnit.publication_id == pid))
        return None if u is None else (u.state, u.reserved_at)


def _library(c):
    return {x["clip_id"] for x in c.get("/api/library").json()["items"]}


def ready(app, client, fake, channel, plan="creator", clips=1):
    who = subscriber(app, client, plan, channel)
    connect(client, who, fake)
    enable(who)
    for i in range(clips):
        live_clip(who.sid, start=100 + 600 * i)
    tick()
    return who


# ---------------------------------------------------------------- 1. deactivated account
def test_a_deactivated_account_never_starts_an_upload(app, client, fake, channel):
    from clipper import customer_publish
    from clipper.db import session_scope
    from clipper.models import User

    who = ready(app, client, fake, channel)
    [p] = pubs(who.uid)
    make_due([p.id])
    with session_scope() as db:
        db.get(User, who.uid).active = False  # blocked by the HTS; plan and switch still on
    with session_scope() as db:
        assert customer_publish.gate(db, who.uid) == ("off", "conta desativada")
    run(p.id)
    assert fake.uploads == [] and pubs(who.uid)[0].status == "waiting"
    assert _unit(p.id) is None, "no unit reserved for a blocked account"
    live_clip(who.sid, start=700)
    tick()
    assert len(pubs(who.uid)) == 1, "nothing new is queued either"
    with session_scope() as db:
        db.get(User, who.uid).active = True
    make_due([p.id])
    run(p.id)
    assert fake.uploads == [p.id], "reactivated: it goes out"


# ---------------------------------------------------------------- 2. reservation lifecycle
def test_reusing_a_released_unit_starts_a_new_reservation_clock(app, client, fake, channel):
    from clipper import customer_publish
    from clipper.db import session_scope, utcnow
    from clipper.models import Publication, PublishUnit

    who = ready(app, client, fake, channel)
    [p] = pubs(who.uid)
    make_due([p.id])
    old = utcnow() - timedelta(hours=10)
    with session_scope() as db:
        db.add(PublishUnit(user_id=who.uid, publication_id=p.id, day="2026-01-01", state="released",
                           created_at=old, reserved_at=old))
    with session_scope() as db:
        assert customer_publish.reserve(db, db.get(Publication, p.id)) == ""
    state, reserved_at = _unit(p.id)
    assert state == "reserved" and reserved_at > utcnow() - timedelta(minutes=1)
    with session_scope() as db:
        assert customer_publish.release_abandoned(db) == 0
    assert _unit(p.id)[0] == "reserved", "a fresh reservation is never released"


def test_cleaner_never_releases_started_or_uncertain_uploads(app, client, fake, channel):
    from clipper import customer_publish
    from clipper.db import session_scope, utcnow
    from clipper.models import PublishUnit

    who = ready(app, client, fake, channel, clips=4)
    a, b, c, d = pubs(who.uid)
    old = utcnow() - timedelta(hours=10)
    with session_scope() as db:
        for p in (a, b, c, d):
            db.add(PublishUnit(user_id=who.uid, publication_id=p.id, day="2026-01-01", state="reserved",
                               created_at=old, reserved_at=old))
    _set(a.id, status="scheduled", attempts=0)  # never sent: abandoned
    _set(b.id, status="scheduled", attempts=1, upload_state={"session": "x"})  # a real attempt, retry pending
    _set(c.id, status="uncertain", attempts=1)  # may be online
    _set(d.id, status="uploading", attempts=1)  # being sent
    with session_scope() as db:
        assert customer_publish.release_abandoned(db) == 1
    assert _unit(a.id)[0] == "released"
    assert _unit(b.id)[0] == "reserved"
    assert _unit(c.id)[0] == "consumed" and _unit(d.id)[0] == "consumed"


def test_race_between_a_worker_reserving_and_the_cleaner(app, client, fake, channel):
    from clipper import customer_publish
    from clipper.db import session_scope, utcnow
    from clipper.models import Publication, PublishUnit

    who = ready(app, client, fake, channel)
    [p] = pubs(who.uid)
    make_due([p.id])
    for _ in range(8):
        old = utcnow() - timedelta(hours=10)
        with session_scope() as db:
            u = db.scalar(select(PublishUnit).where(PublishUnit.publication_id == p.id))
            if u is None:
                db.add(PublishUnit(user_id=who.uid, publication_id=p.id, day="2026-01-01", state="released",
                                   created_at=old, reserved_at=old))
            else:
                u.state, u.reserved_at = "released", old
        barrier = threading.Barrier(2)
        out = {}

        def worker():
            barrier.wait()
            with session_scope() as db:
                out["reserve"] = customer_publish.reserve(db, db.get(Publication, p.id))

        def cleaner():
            barrier.wait()
            with session_scope() as db:
                out["released"] = customer_publish.release_abandoned(db)
        threads = [threading.Thread(target=worker), threading.Thread(target=cleaner)]
        [t.start() for t in threads]
        [t.join() for t in threads]
        assert out["reserve"] == ""
        assert _unit(p.id)[0] == "reserved", "whatever the order, the worker keeps its reservation"


# ---------------------------------------------------------------- 3. retention (waiting and scheduled)
def test_retention_expires_waiting_proposed_and_scheduled_but_keeps_the_library(app, client, fake, channel):
    from clipper.db import utcnow

    who = ready(app, client, fake, channel, clips=4)
    a, b, c, d = pubs(who.uid)
    eight = utcnow() - timedelta(days=8)
    _set(a.id, status="waiting", created_at=eight, scheduled_at=None)
    _set(b.id, status="scheduled", created_at=eight, scheduled_at=utcnow() + timedelta(hours=1))
    _set(c.id, status="proposed", created_at=eight)
    _set(d.id, status="scheduled", created_at=eight, attempts=1, upload_state={"session": "x"})  # retry of a real attempt
    tick()
    states = {p.id: p.status for p in pubs(who.uid)}
    assert states[a.id] == states[b.id] == states[c.id] == "expired"
    assert states[d.id] == "scheduled", "an upload already started is not expired"
    assert {a.clip_id, b.clip_id, c.clip_id} <= _library(who.c), "the Shorts stay in the library"


def test_a_scheduled_publication_past_its_deadline_expires_at_upload_time(app, client, fake, channel):
    from clipper.db import utcnow

    who = ready(app, client, fake, channel)
    [p] = pubs(who.uid)
    _set(p.id, status="scheduled", scheduled_at=utcnow(), created_at=utcnow() - timedelta(days=7, minutes=5))
    run(p.id)
    assert pubs(who.uid)[0].status == "expired" and fake.uploads == [] and _unit(p.id) is None


def test_the_planner_never_schedules_past_the_deadline(app, client, fake, channel):
    from clipper import customer_publish
    from clipper.db import session_scope, utcnow
    from clipper.models import Publication

    who = ready(app, client, fake, channel, clips=6)
    created = utcnow() - timedelta(days=6, hours=20)  # about 4 hours left
    for p in pubs(who.uid):
        _set(p.id, status="waiting", scheduled_at=None, created_at=created)
    with session_scope() as db:
        customer_publish.plan_waiting(db, who.uid)
    with session_scope() as db:
        rows = db.scalars(select(Publication).where(Publication.owner_id == who.uid)).all()
        scheduled = [r for r in rows if r.status == "scheduled"]
        assert all(r.scheduled_at <= customer_publish.deadline(r) for r in scheduled)
        assert len(scheduled) <= 3 and any(r.status == "waiting" for r in rows), "the rest waits and will expire"


# ---------------------------------------------------------------- 4. removed channels
def test_removing_a_channel_stops_its_automatic_publications(app, client, fake, channel):
    who = ready(app, client, fake, channel, clips=3)
    first, second, third = pubs(who.uid)
    _set(third.id, status="uploading", attempts=1)  # already being sent: finishes its course
    make_due([first.id])
    monitor = who.c.get("/api/account/channels").json()[0]["id"]
    library_before = _library(who.c)
    assert who.c.delete(f"/api/account/channels/{monitor}", headers=CSRF).status_code == 200
    states = {p.id: p.status for p in pubs(who.uid)}
    assert states[first.id] == states[second.id] == "cancelled"
    assert states[third.id] == "uploading"
    assert all(_unit(p)[0] == "released" for p in (first.id,) if _unit(p))
    assert _library(who.c) == library_before, "delivered Shorts are kept"
    rules = who.c.get("/api/account/publishing").json()["rules"]
    assert rules and all(r["enabled"] is False for r in rules), "its rules are switched off"
    # Re-enabling the rule by hand does not bring the removed channel back
    tick()
    assert len(pubs(who.uid)) == 3


def test_old_shares_of_a_removed_monitor_are_never_published_even_if_followed_again(app, client, fake, channel):
    from clipper import customer_publish
    from clipper.db import session_scope
    from clipper.models import Publication

    who = ready(app, client, fake, channel)
    [old] = pubs(who.uid)
    monitor = who.c.get("/api/account/channels").json()[0]["id"]
    who.c.delete(f"/api/account/channels/{monitor}", headers=CSRF)
    # A queued publication of that channel that escaped the cancellation is refused at upload time
    _set(old.id, status="scheduled")
    with session_scope() as db:
        verdict, why = customer_publish.before_upload(db, db.get(Publication, old.id))
    assert verdict == "refuse" and "deixou de acompanhar" in why
    # Following the channel again: the old Shorts (other monitor) are not published, new ones are
    assert who.c.post("/api/account/channels", json={"url": channel}, headers=CSRF).status_code == 201
    rule = who.c.get("/api/account/publishing").json()["rules"][0]
    body = {"account_id": rule["account_id"], "streamer_id": who.sid, "enabled": True, "require_approval": False,
            "min_score": 50, "privacy": "private", "slots": ["09:00", "12:00", "15:00"], "rights_confirmed": True}
    assert who.c.put(f"/api/account/publishing/rules/{rule['id']}", json=body, headers=CSRF).status_code == 200
    tick()
    assert [p.id for p in pubs(who.uid)] == [old.id]
    new_clip = live_clip(who.sid, start=1300)
    tick()
    assert {p.clip_id for p in pubs(who.uid)} == {old.clip_id, new_clip}


def test_race_between_channel_removal_and_the_scan(app, client, fake, channel):
    from clipper import customer_publish
    from clipper.db import session_scope
    from clipper.models import Publication

    who = subscriber(app, client, "pro", channel)
    connect(client, who, fake)
    enable(who)
    for i in range(3):
        live_clip(who.sid, start=100 + 600 * i)
    monitor = who.c.get("/api/account/channels").json()[0]["id"]
    barrier = threading.Barrier(2)

    def remove():
        barrier.wait()
        who.c.delete(f"/api/account/channels/{monitor}", headers=CSRF)

    def scan():
        barrier.wait()
        tick()
    threads = [threading.Thread(target=remove), threading.Thread(target=scan)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    # Whatever the order: nothing of the removed channel can still be uploaded
    with session_scope() as db:
        for p in db.scalars(select(Publication).where(Publication.owner_id == who.uid)):
            if p.status in ("proposed", "waiting", "scheduled"):
                assert customer_publish.before_upload(db, p)[0] == "refuse"
            else:
                assert p.status == "cancelled"


def test_plan_limits_unchanged():
    from clipper import catalog

    for plan, (destinations, daily) in {"basic": (0, 0), "creator": (2, 3), "pro": (4, 4)}.items():
        f = catalog.limits_for(plan)["features"]
        assert (f["publishDestinations"], f["publishDaily"]) == (destinations, daily)
        assert f["autoPublish"] is (plan != "basic")


def test_shorts_from_a_previous_follow_are_not_published_after_following_again(app, client, fake, channel):
    who = subscriber(app, client, "creator", channel)
    connect(client, who, fake)
    enable(who)
    who.c.put("/api/account/publishing/settings", json={"enabled": False}, headers=CSRF)  # paused: nothing queued
    old_clip = live_clip(who.sid, start=100)
    tick()
    assert pubs(who.uid) == []
    monitor = who.c.get("/api/account/channels").json()[0]["id"]
    who.c.delete(f"/api/account/channels/{monitor}", headers=CSRF)
    assert who.c.post("/api/account/channels", json={"url": channel}, headers=CSRF).status_code == 201
    rule = who.c.get("/api/account/publishing").json()["rules"][0]
    who.c.put(f"/api/account/publishing/rules/{rule['id']}", json={
        "account_id": rule["account_id"], "streamer_id": who.sid, "enabled": True, "require_approval": False,
        "min_score": 50, "privacy": "private", "slots": ["09:00"], "rights_confirmed": True}, headers=CSRF)
    who.c.put("/api/account/publishing/settings", json={"enabled": True}, headers=CSRF)
    tick()
    assert old_clip in _library(who.c), "still in the library"
    assert pubs(who.uid) == [], "delivered under the removed follow: never published automatically"
