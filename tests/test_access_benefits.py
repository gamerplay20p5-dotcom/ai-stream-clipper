"""HTS access benefits: access credits (compensation days) and temporary upgrades (benefits.py, migration 0020).

Never money: payments, snapshots and the commercial service are untouched. Covers preview, idempotency, renewals,
natural end, refund/suspension/review, precedence with paid and courtesy plans, quotas kept in the same period,
expiry by time, revocation, concurrency, permissions and the audited history.
"""

from __future__ import annotations

import threading
import uuid
from datetime import timedelta

from fastapi.testclient import TestClient
from sqlalchemy import func, select

from conftest import CSRF
from test_billing_entitlements import DAY, _now, billing_env, customer, deliver  # noqa: F401


def limits(plan: str) -> dict:
    from clipper import catalog

    return catalog.limits_for(plan)


def snap(uid, *, seq=1, plan="creator", status="active", until=None, start=None, review="", financial=None,
         payments=None, ref=None):
    now = _now()
    return {"event_id": uuid.uuid4().hex, "seq": seq, "provider": "stripe", "subscription_ref": ref or f"sub_b{uid}",
            "account_id": uid, "plan": plan, "market": "US", "financial_status": financial or status, "review": review,
            "access": {"status": status, "until": until if until is not None else now + 30 * DAY,
                       "period_start": start if start is not None else now - DAY, "period_end": now + 29 * DAY,
                       "renews_at": None, "cancel_at_period_end": False},
            "limits": limits(plan), "manage": "stripe_portal", "renewal": None,
            "payments": payments if payments is not None else [{"id": "in_1", "at": now, "amount": 4499, "currency": "usd", "status": "paid"}]}


def paid(app, client, plan="creator", **kw):
    uid, c = customer(app)
    assert deliver(client, snap(uid, plan=plan, **kw)).json()["outcome"] == "applied"
    return uid, c


def admin_client(app):
    a = TestClient(app)
    assert a.post("/api/auth/login", json={"email": "admin@example.com", "password": "correct-horse-battery"}).status_code == 200
    return a


def key() -> str:
    return uuid.uuid4().hex


def ent(uid, **kw):
    from clipper import entitlements
    from clipper.db import session_scope

    with session_scope() as db:
        return entitlements.current(db, uid, **kw)


def give_credit(a, uid, days=3, reason="queda do servidor em 20/11", request_key=None):
    return a.post(f"/api/admin/customers/{uid}/credits", json={"days": days, "reason": reason,
                                                              "request_key": request_key or key()}, headers=CSRF)


def give_upgrade(a, uid, plan="pro", days=7, request_key=None):
    return a.post(f"/api/admin/customers/{uid}/upgrades", json={"plan": plan, "days": days, "reason": "teste do Pro por uma semana",
                                                               "request_key": request_key or key()}, headers=CSRF)


# ------------------------------------------------------------------ access credits
def test_credit_preview_then_grant_adds_days_without_touching_payments(app, client):
    from clipper.db import session_scope
    from clipper.models import AccessBenefit, AccountSubscription

    uid, _ = paid(app, client)
    a = admin_client(app)
    before = ent(uid).until
    p = a.post(f"/api/admin/customers/{uid}/credits/preview", json={"days": 3}, headers=CSRF)
    assert p.status_code == 200 and p.json()["applies"] is True
    assert p.json()["after"]["until"] > p.json()["before"]["until"]
    with session_scope() as db:
        assert db.scalar(select(func.count()).select_from(AccessBenefit).where(AccessBenefit.user_id == uid)) == 0, "preview changes nothing"
        snapshot_before = db.scalar(select(AccountSubscription).where(AccountSubscription.user_id == uid))
        frozen = (snapshot_before.access_until, snapshot_before.renews_at, snapshot_before.payments, snapshot_before.last_seq)
    r = give_credit(a, uid)
    assert r.status_code == 201, r.text
    assert r.json()["kind"] == "credit" and r.json()["access"]["credit_days"] == 3
    e = ent(uid)
    assert e.until == before + timedelta(days=3) and e.credit_days == 3 and e.plan == "creator"
    with session_scope() as db:
        row = db.scalar(select(AccountSubscription).where(AccountSubscription.user_id == uid))
        assert (row.access_until, row.renews_at, row.payments, row.last_seq) == frozen, "the paid snapshot is untouched"


def test_repeated_click_gives_the_credit_once(app, client):
    from clipper.db import session_scope
    from clipper.models import AccessBenefit

    uid, _ = paid(app, client)
    a = admin_client(app)
    k = key()
    first = give_credit(a, uid, request_key=k)
    again = give_credit(a, uid, request_key=k)
    assert first.status_code == again.status_code == 201
    assert again.json()["id"] == first.json()["id"] and again.json()["duplicate"] is True
    with session_scope() as db:
        assert db.scalar(select(func.count()).select_from(AccessBenefit).where(AccessBenefit.user_id == uid)) == 1
    assert ent(uid).credit_days == 3
    # the same key can never be reused for another customer or another kind
    other, _ = paid(app, client)
    assert give_credit(a, other, request_key=k).status_code == 422
    assert give_upgrade(a, uid, request_key=k).status_code == 422


def test_simultaneous_requests_with_the_same_key_create_one_credit(app, client):
    from clipper.db import session_scope
    from clipper.models import AccessBenefit

    uid, _ = paid(app, client)
    a = admin_client(app)
    k = key()
    results = []
    barrier = threading.Barrier(4)

    def hit():
        barrier.wait()
        results.append(give_credit(a, uid, request_key=k).status_code)

    threads = [threading.Thread(target=hit) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert set(results) <= {201}, results
    with session_scope() as db:
        assert db.scalar(select(func.count()).select_from(AccessBenefit).where(AccessBenefit.user_id == uid)) == 1
    assert ent(uid).credit_days == 3


def test_credit_survives_renewal_and_does_not_postpone_the_charge(app, client):
    from clipper.db import session_scope
    from clipper.models import AccountSubscription

    now = _now()
    uid, _ = paid(app, client, until=now + 10 * DAY)
    a = admin_client(app)
    give_credit(a, uid, days=5)
    # renewal: the provider keeps ITS date (no postponement) and the credit moves with the new period
    assert deliver(client, snap(uid, seq=2, until=now + 40 * DAY, start=now + 10 * DAY)).json()["outcome"] == "applied"
    e = ent(uid)
    with session_scope() as db:
        row = db.scalar(select(AccountSubscription).where(AccountSubscription.user_id == uid))
        assert row.access_until == e.until - timedelta(days=5), "the provider date is never changed by the credit"
    assert e.credit_days == 5


def test_credit_runs_after_a_natural_end_without_resetting_quotas(app, client):
    from clipper import entitlements
    from clipper.db import session_scope

    uid, _ = paid(app, client)
    a = admin_client(app)
    e0 = ent(uid)
    with session_scope() as db:
        entitlements.record_usage(db, uid, "live_seconds", f"t:{uid}:1", e0.period_key, 3600)
    give_credit(a, uid, days=4)
    # the customer canceled: Stripe ends the subscription now (an ended snapshot carries no period dates)
    ended = snap(uid, seq=2, status="ended", financial="canceled", until=0, start=0)
    ended["access"]["period_end"] = None
    assert deliver(client, ended).json()["outcome"] == "applied"
    e = ent(uid)
    assert e is not None and e.credit_days == 4 and e.plan == "creator"
    assert e.period_key == e0.period_key, "same period: the hours already consumed still count"
    with session_scope() as db:
        assert entitlements.used(db, e, "live_seconds") == 3600
    left = e.until - entitlements.utcnow() if hasattr(entitlements, "utcnow") else None
    assert left is None or left <= timedelta(days=4, minutes=1), "an immediate end never gives back the rest of the period"


def test_credit_never_revives_refunded_suspended_or_reviewed_subscriptions(app, client):
    a = admin_client(app)
    refunded, _ = paid(app, client)
    give_credit(a, refunded)
    deliver(client, snap(refunded, seq=2, status="ended", financial="refunded", until=0))
    assert ent(refunded) is None

    suspended, _ = paid(app, client)
    give_credit(a, suspended)
    deliver(client, snap(suspended, seq=2, status="suspended", financial="paused", until=0))
    assert ent(suspended) is None

    reviewed, _ = paid(app, client)
    give_credit(a, reviewed)
    deliver(client, snap(reviewed, seq=2, review="dispute"))
    e = ent(reviewed)
    assert e is not None and e.credit_days == 0, "during a review only the paid access counts"
    # a credit cannot be given while it would change nothing
    assert give_credit(a, reviewed).status_code == 422


def test_credit_needs_a_paid_subscription(app, client):
    uid, _ = customer(app)
    a = admin_client(app)
    assert a.post(f"/api/admin/customers/{uid}/grants", json={"plan": "creator", "days": 7, "reason": "cortesia de teste"},
                  headers=CSRF).status_code == 201
    r = give_credit(a, uid)
    assert r.status_code == 422 and "cortesia" in r.json()["detail"]


def test_revoking_a_credit_restores_the_paid_date(app, client):
    uid, _ = paid(app, client)
    a = admin_client(app)
    paid_until = ent(uid).until
    b = give_credit(a, uid, days=6).json()
    assert ent(uid).until == paid_until + timedelta(days=6)
    assert a.post(f"/api/admin/customers/{uid}/benefits/{b['id']}/revoke", json={"reason": "dado por engano"},
                  headers=CSRF).json()["state"] == "revoked"
    assert ent(uid).until == paid_until
    # revoking twice is harmless; another customer's benefit is not reachable
    assert a.post(f"/api/admin/customers/{uid}/benefits/{b['id']}/revoke", json={"reason": "de novo, sem efeito"},
                  headers=CSRF).status_code == 200
    other, _ = paid(app, client)
    assert a.post(f"/api/admin/customers/{other}/benefits/{b['id']}/revoke", json={"reason": "cliente errado"},
                  headers=CSRF).status_code == 404


# ------------------------------------------------------------------ temporary upgrades
def test_temporary_upgrade_uses_pro_limits_in_the_same_period(app, client):
    from clipper import entitlements
    from clipper.db import session_scope

    uid, _ = paid(app, client, plan="creator")
    a = admin_client(app)
    e0 = ent(uid)
    with session_scope() as db:
        entitlements.record_usage(db, uid, "live_seconds", f"u:{uid}:1", e0.period_key, 90 * 3600)
    r = give_upgrade(a, uid)
    assert r.status_code == 201, r.text
    e = ent(uid)
    assert (e.plan, e.base_plan) == ("pro", "creator") and e.limits["hours"] == 200 and e.limits["channels"] == 4
    assert e.period_key == e0.period_key and e.until == e0.until, "same period, same access date"
    with session_scope() as db:
        assert entitlements.used(db, e, "live_seconds") == 90 * 3600, "quotas are not reset by the upgrade"
    # a renewal webhook neither removes nor extends the upgrade
    deliver(client, snap(uid, seq=2, until=_now() + 60 * DAY))
    e2 = ent(uid)
    assert e2.plan == "pro" and e2.upgrade_until == e.upgrade_until
    # after the end date: back to the paid Creator automatically
    later = ent(uid, now=e.upgrade_until + timedelta(minutes=1))
    assert (later.plan, later.limits["hours"]) == ("creator", 100)


def test_upgrade_rules(app, client):
    a = admin_client(app)
    uid, _ = paid(app, client, plan="creator")
    assert give_upgrade(a, uid, plan="creator").status_code == 422, "must be above the current plan"
    k = key()
    first = give_upgrade(a, uid, request_key=k)
    assert first.status_code == 201
    assert give_upgrade(a, uid, request_key=k).json()["id"] == first.json()["id"], "repeated click: same upgrade"
    assert give_upgrade(a, uid).status_code == 422, "one active upgrade at a time"
    # revocation: back to Creator at once
    assert a.post(f"/api/admin/customers/{uid}/benefits/{first.json()['id']}/revoke", json={"reason": "cliente pediu"},
                  headers=CSRF).status_code == 200
    assert ent(uid).plan == "creator"
    # never for an account without access (suspended): nothing is reactivated
    s, _ = paid(app, client, plan="basic")
    deliver(client, snap(s, plan="basic", seq=2, status="suspended", financial="paused", until=0))
    assert give_upgrade(a, s, plan="creator").status_code == 422
    # an upgrade given before a suspension does not keep access either
    t, _ = paid(app, client, plan="basic")
    give_upgrade(a, t, plan="pro")
    deliver(client, snap(t, plan="basic", seq=2, status="suspended", financial="paused", until=0))
    assert ent(t) is None


def test_paid_pro_wins_over_an_upgrade_and_courtesy_keeps_its_rules(app, client):
    a = admin_client(app)
    uid, _ = paid(app, client, plan="creator")
    give_upgrade(a, uid, plan="pro")
    # the customer buys Pro: the paid plan is used, the upgrade has nothing left to add
    deliver(client, snap(uid, plan="pro", seq=2))
    e = ent(uid)
    assert (e.plan, e.base_plan, e.upgrade_until) == ("pro", "pro", None)
    # courtesy (existing feature) still works next to the benefits
    c, _ = paid(app, client, plan="basic")
    assert a.post(f"/api/admin/customers/{c}/grants", json={"plan": "creator", "days": 14, "reason": "parceria"},
                  headers=CSRF).status_code == 201
    assert ent(c).plan == "creator" and ent(c).provider == "hts"


def test_upgrade_end_pauses_channels_beyond_the_paid_plan(app, client):
    from clipper import benefits
    from clipper.db import session_scope, utcnow
    from clipper.models import AccessBenefit, ChannelMonitor, Streamer

    uid, _ = paid(app, client, plan="creator")
    a = admin_client(app)
    bid = give_upgrade(a, uid, plan="pro").json()["id"]
    with session_scope() as db:
        for i in range(4):
            s = Streamer(platform="twitch", channel=f"up{uuid.uuid4().hex[:8]}", url="https://www.twitch.tv/x", hts_monitoring=False)
            db.add(s)
            db.flush()
            db.add(ChannelMonitor(user_id=uid, streamer_id=s.id, preset="balanced"))
    with session_scope() as db:
        __import__("clipper.entitlements", fromlist=["x"]).enforce_account(db, uid)
        assert all(not m.paused_reason for m in db.scalars(select(ChannelMonitor).where(ChannelMonitor.user_id == uid)))
        db.get(AccessBenefit, bid).ends_at = utcnow() - timedelta(minutes=1)  # the week is over
    with session_scope() as db:
        assert benefits.enforce_boundaries(db) >= 1
    with session_scope() as db:
        paused = [m for m in db.scalars(select(ChannelMonitor).where(ChannelMonitor.user_id == uid)) if m.paused_reason]
        assert len(paused) == 2, "Creator follows 2 channels: the 2 newest pause, nothing is deleted"


# ------------------------------------------------------------------ permissions, validation and history
def test_only_admins_with_csrf_and_valid_input(app, client):
    uid, c = paid(app, client)
    a = admin_client(app)
    assert c.post(f"/api/admin/customers/{uid}/credits", json={"days": 3, "reason": "a mim mesmo", "request_key": key()},
                  headers=CSRF).status_code == 403, "a customer never gives itself benefits"
    assert a.post(f"/api/admin/customers/{uid}/credits", json={"days": 3, "reason": "sem csrf", "request_key": key()}).status_code == 403
    for body in ({"days": 0, "reason": "zero dias", "request_key": key()}, {"days": 91, "reason": "demais", "request_key": key()},
                 {"days": 3, "reason": "x", "request_key": key()}, {"days": 3, "reason": "sem chave", "request_key": "curta"}):
        assert a.post(f"/api/admin/customers/{uid}/credits", json=body, headers=CSRF).status_code == 422
    assert a.post(f"/api/admin/customers/{uid}/upgrades", json={"plan": "basic", "days": 7, "reason": "plano inválido",
                                                                "request_key": key()}, headers=CSRF).status_code == 422
    from clipper.db import session_scope
    from clipper.models import User

    with session_scope() as db:
        staff_id = db.scalar(select(User.id).where(User.email == "admin@example.com"))
    assert give_credit(a, staff_id).status_code == 404, "never to staff accounts"
    assert give_upgrade(a, staff_id).status_code == 404


def test_history_and_panel_show_paid_plan_benefit_and_dates(app, client):
    uid, _ = paid(app, client, plan="creator")
    a = admin_client(app)
    give_credit(a, uid, days=2)
    give_upgrade(a, uid, plan="pro", days=7)
    d = a.get(f"/api/admin/customers/{uid}").json()
    assert d["access"]["paid_plan"] == "creator" and d["access"]["plan"] == "pro"
    assert d["access"]["credit_days"] == 2 and d["access"]["upgrade_until"]
    assert {b["kind"] for b in d["benefits"]} == {"credit", "upgrade"}
    h = a.get(f"/api/admin/customers/{uid}/history").json()
    assert [x["action"] for x in h][:2] == ["admin.temporary_upgrade", "admin.access_credit"]
    assert all(x["by"] == "admin@example.com" for x in h)
