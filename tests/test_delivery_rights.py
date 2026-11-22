"""Delivery rights per PAID period (owner decision, PR #24 review):

* a Short fully covered by a validly paid period is delivered even when it is rendered after expiry, a normal
  cancellation or an operational suspension;
* never after a full refund of that period (refund, chargeback, dispute lost);
* while the subscription is under financial review or an open dispute, deliveries wait and are released when the
  review is resolved;
* plan, preset and limits are those of the period that paid for the moment, never of a later subscription, and
  no right is granted over hours that were never paid;
* a security block (account deactivated) stops deliveries; Shorts already delivered are kept.
"""

from __future__ import annotations

import uuid

import pytest

from conftest import CSRF
from test_billing_entitlements import DAY, _now, billing_env, customer, deliver  # noqa: F401
from test_shared_lives import PLANS, channel_url  # noqa: F401


def snap(uid, seq, *, plan="pro", start, end, until=None, status="active", financial=None, review="", payments=None):
    return {
        "event_id": uuid.uuid4().hex, "seq": seq, "provider": "stripe", "subscription_ref": f"sub_{uid}", "account_id": uid,
        "plan": plan, "market": "US", "financial_status": financial or status, "review": review,
        "access": {"status": status, "until": until if until is not None else end + DAY, "period_start": start,
                   "period_end": end, "renews_at": None, "cancel_at_period_end": False},
        "limits": PLANS[plan], "manage": "stripe_portal", "renewal": None, "payments": payments or [],
    }


class World:
    """One customer-only channel, live, with helpers for segments (600 s), clips and libraries."""

    def __init__(self, app, client, url):
        self.app, self.client, self.url = app, client, url
        self.now = _now()
        self.start, self.end = self.now - DAY, self.now + 29 * DAY
        self.sid = None
        self.sess = None

    def customer(self, plan="pro", preset="balanced", **kw):
        uid, c = customer(self.app)
        assert deliver(self.client, snap(uid, 1, plan=plan, start=self.start, end=self.end, **kw)).status_code == 200
        r = c.post("/api/account/channels", json={"url": self.url, "preset": preset}, headers=CSRF)
        assert r.status_code == 201, r.text
        return uid, c, r.json()["id"]

    def go_live(self):
        from clipper import pipeline
        from clipper.db import session_scope
        from clipper.models import Streamer
        from sqlalchemy import select

        with session_scope() as db:
            self.sid = db.scalar(select(Streamer.id).where(Streamer.channel == self.url.rsplit("/", 1)[-1]))
            self.sess = pipeline.report_status(db, self.sid, live=True)["session_id"]

    def seg(self, idx):
        from clipper import pipeline
        from clipper.db import session_scope

        with session_scope() as db:
            pipeline.register_segments(db, self.sess, "w1", "http://seg", [{"idx": idx, "start": idx * 600.0, "duration": 600.0}])

    def clip(self, start, end, score=80, breakdown=None):
        from clipper import sharing
        from clipper.db import session_scope
        from clipper.models import Clip

        with session_scope() as db:
            row = Clip(session_id=self.sess, streamer_id=self.sid, start=start, end=end, peak=start + 5, score=score,
                       status="ready", video_path=f"clips/{uuid.uuid4().hex}/v1.mp4", file_size=1_000_000,
                       score_breakdown=breakdown or {})
            db.add(row)
            db.flush()
            sharing.on_clip_status(db, row)
            return row.id

    def update(self, uid, seq, **kw):
        kw.setdefault("start", self.start)
        kw.setdefault("end", self.end)
        r = deliver(self.client, snap(uid, seq, **kw))
        assert r.status_code == 200 and r.json()["outcome"] == "applied", r.text


def library(c) -> set[int]:
    r = c.get("/api/library")
    assert r.status_code == 200, r.text
    return {x["clip_id"] for x in r.json()["items"]}


@pytest.fixture
def world(app, client, channel_url):
    return World(app, client, channel_url)


def test_review_on_an_active_subscription_holds_deliveries_until_resolved(world):
    from clipper import entitlements
    from clipper.db import session_scope
    from clipper.models import Clip, Streamer

    a, ca, _ = world.customer()
    b, cb, _ = world.customer()
    world.go_live()
    world.seg(0)
    # Partial refund: access stays active, but the payment is under financial review
    world.update(a, 2, review="partial_refund")
    held = world.clip(100, 130)
    assert held not in library(ca), "no delivery during a financial review, even with active access"
    assert held in library(cb), "other customers are not affected"
    with session_scope() as db:
        assert entitlements.render_block_reason(db, db.get(Streamer, world.sid), db.get(Clip, held)) == ""
    # Review cleared: the waiting Short is released automatically, once
    world.update(a, 3, review="")
    assert held in library(ca)
    world.update(a, 4)
    assert [x for x in ca.get("/api/library").json()["items"] if x["clip_id"] == held].__len__() == 1


def test_open_dispute_holds_and_a_lost_dispute_never_delivers(world):
    a, ca, _ = world.customer()
    world.go_live()
    world.seg(0)
    before = world.clip(10, 40)  # delivered before the dispute: kept
    world.update(a, 2, review="dispute")
    held = world.clip(100, 130)
    assert held not in library(ca)
    world.update(a, 3, status="ended", financial="refunded", review="dispute_lost", until=world.now - 1)
    assert held not in library(ca), "dispute lost = refunded period"
    assert before in library(ca), "legitimately delivered Shorts are preserved"


def test_full_refund_of_the_period_blocks_late_renders_but_keeps_earlier_deliveries(world):
    a, ca, _ = world.customer()
    world.go_live()
    world.seg(0)
    early = world.clip(10, 40)
    world.update(a, 2, status="ended", financial="refunded", until=world.now - 1)
    late = world.clip(100, 130)  # captured in the refunded period, rendered after the refund
    lib = library(ca)
    assert early in lib and late not in lib


def test_cancellation_expiry_and_operational_suspension_still_deliver_paid_moments(world):
    canceled, cc, _ = world.customer()
    expired, ce, _ = world.customer()
    suspended, cs, _ = world.customer()
    world.go_live()
    world.seg(0)
    world.update(canceled, 2, status="ended", financial="canceled", until=world.now - 1)
    world.update(expired, 2, until=world.now - 1)  # time ran out
    world.update(suspended, 2, status="suspended", financial="past_due")
    late = world.clip(100, 130)
    for c in (cc, ce, cs):
        assert late in library(c)
    # Nothing after the stop: the next segment is not followed, so a later moment is not theirs
    world.seg(1)
    after = world.clip(700, 730)
    for c in (cc, ce, cs):
        assert after not in library(c)


def test_plan_change_applies_the_plan_of_the_period_that_paid_for_the_moment(world):
    from clipper import entitlements
    from clipper.db import session_scope
    from clipper.models import Clip, Streamer

    a, ca, monitor = world.customer(plan="basic")
    world.go_live()
    world.seg(0)  # paid under Basic
    world.update(a, 2, plan="pro")  # upgrade inside the same period
    assert ca.patch(f"/api/account/channels/{monitor}", json={"preset": "humor"}, headers=CSRF).status_code == 200
    world.seg(1)  # paid under Pro
    funny = {"humor": 9, "reaction": 9, "audience": 9, "intensity": 1, "surprise": 1}
    basic_moment = world.clip(100, 130, score=50, breakdown=funny)  # balanced (Basic): 50 < 55
    pro_moment = world.clip(700, 730, score=50, breakdown=funny)  # humor (Pro): 70 >= 55
    lib = library(ca)
    assert basic_moment not in lib and pro_moment in lib
    with session_scope() as db:
        streamer = db.get(Streamer, world.sid)
        assert entitlements.has_feature(db, streamer, "secondPass", db.get(Clip, basic_moment)) is False
        assert entitlements.has_feature(db, streamer, "secondPass", db.get(Clip, pro_moment)) is True


def test_renewal_and_refund_of_a_past_period_only_affect_that_period(world):
    a, ca, _ = world.customer()
    p1_start, p1_end = world.now - 30 * DAY, world.now - 120
    world.update(a, 2, start=p1_start, end=p1_end, until=world.now + DAY)  # period 1 (in grace window)
    world.go_live()
    world.seg(0)  # charged to period 1
    p2_start, p2_end = world.now - 60, world.now + 30 * DAY
    world.update(a, 3, start=p2_start, end=p2_end, payments=[{"id": "in_1", "at": p1_start + 10, "status": "paid"}])
    world.seg(1)  # charged to period 2 (renewal)
    # Month 1 refunded afterwards (InfinitePay style: one charge refunded, the subscription goes on)
    world.update(a, 4, start=p2_start, end=p2_end, payments=[
        {"id": "in_1", "at": p1_start + 10, "status": "refunded"}, {"id": "in_2", "at": p2_start + 10, "status": "paid"}])
    month1 = world.clip(100, 130)
    month2 = world.clip(700, 730)
    across = world.clip(590, 620)
    lib = library(ca)
    assert month1 not in lib and across not in lib, "the refunded period grants nothing"
    assert month2 in lib, "the renewed, paid period is not affected"


def test_no_rights_over_hours_never_paid(world):
    a, ca, _ = world.customer()
    world.update(a, 2, status="ended", financial="canceled", until=world.now - 1)  # no access when the live starts
    other, co, _ = world.customer()  # keeps the live going
    world.go_live()
    world.seg(0)
    world.seg(1)
    # Renews in the middle of the live: followed from the next segment only
    world.update(a, 3, start=world.now, end=world.now + 30 * DAY)
    world.seg(2)
    unpaid = world.clip(700, 730)
    paid = world.clip(1300, 1330)
    lib = library(ca)
    assert unpaid not in lib and paid in lib
    assert unpaid in library(co) and paid in library(co)


def test_security_block_stops_deliveries_and_keeps_the_library(world, client):
    from clipper import editorial
    from clipper.db import session_scope
    from clipper.models import User

    a, ca, _ = world.customer()
    world.go_live()
    world.seg(0)
    first = world.clip(10, 40)
    with session_scope() as db:
        db.get(User, a).active = False  # blocked by the HTS for security
    blocked = world.clip(100, 130)
    assert ca.get("/api/library").status_code == 401, "a blocked account reaches nothing"
    with session_scope() as db:
        db.get(User, a).active = True
        editorial.redeliver_for_user(db, a)  # the next snapshot or the operator releases what is due
    lib = library(ca)
    assert first in lib and blocked in lib


def test_refund_snapshot_with_shifted_period_dates_still_revokes_the_latest_paid_period(world):
    a, ca, _ = world.customer()
    world.go_live()
    world.seg(0)
    # The refund snapshot carries slightly different period dates than the paid one
    world.update(a, 2, start=world.start + 5, end=world.end + 5, status="ended", financial="refunded", until=world.now - 1)
    assert world.clip(100, 130) not in library(ca)
