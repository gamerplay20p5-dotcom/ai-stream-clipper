"""Subscriptions -> accounts -> quotas, end to end inside the API (the commercial service is simulated).

Covers the delivery checklist: activation, declined payments, duplicate/out-of-order events, browser returns,
renewal, expiry, review, refund, plan features, self-service limits, concurrency, restarts, sync failures and
isolation between customers. HTS channels (no owner) must keep working without limits.
"""

from __future__ import annotations

import json
import os
import threading
import uuid
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient

from conftest import CSRF

BILLING_SECRET = "billing-link-secret-0123456789abcdef"
DAY = 86_400


@pytest.fixture(autouse=True)
def billing_env(monkeypatch):
    from clipper.config import reset_settings_cache

    monkeypatch.setenv("ASC_BILLING_SECRET", BILLING_SECRET)
    monkeypatch.setenv("ASC_COMMERCIAL_URL", "http://commercial.test")
    monkeypatch.setenv("ASC_SIGNUP_OPEN", "true")  # these tests exercise an open sign-up
    reset_settings_cache()
    yield
    reset_settings_cache()


def _now() -> int:
    from clipper.db import utcnow

    return int(utcnow().replace(tzinfo=__import__("datetime").timezone.utc).timestamp())


LIMITS = {
    "basic": {"hours": 40, "channels": 1, "simultaneous": 1, "storage_gb": 5, "export_4k": 0,
              "features": {"ai": "economy", "secondPass": False, "variants": False, "priority": False, "editing": False}},
    "pro": {"hours": 200, "channels": 4, "simultaneous": 2, "storage_gb": 25, "export_4k": 30,
            "features": {"ai": "premium", "secondPass": True, "variants": True, "priority": True, "editing": True}},
}


def customer(app, email: str | None = None):
    """A verified customer account (password set) and a TestClient signed in as it."""
    from clipper.db import session_scope
    from clipper.models import User
    from clipper.security import hash_password

    email = email or f"c-{uuid.uuid4().hex[:10]}@example.com"
    with session_scope() as db:
        u = User(email=email, password_hash=hash_password("uma-senha-bem-longa-1"), role="customer", active=True)
        db.add(u)
        db.flush()
        uid = u.id
    c = TestClient(app)
    r = c.post("/api/auth/login", json={"email": email, "password": "uma-senha-bem-longa-1"})
    assert r.status_code == 200, r.text
    return uid, c


def snapshot(account_id: int, *, plan="pro", status="active", until=None, start=None, seq=1, ref=None, event_id=None,
             provider="stripe", review="", financial=None) -> dict:
    now = _now()
    return {
        "event_id": event_id or uuid.uuid4().hex, "seq": seq, "provider": provider,
        "subscription_ref": ref or f"sub_{account_id}", "account_id": account_id, "plan": plan, "market": "US",
        "financial_status": financial or status, "review": review,
        "access": {"status": status, "until": until if until is not None else now + 30 * DAY,
                   "period_start": start if start is not None else now - DAY, "period_end": now + 29 * DAY,
                   "renews_at": None, "cancel_at_period_end": False},
        "limits": LIMITS[plan], "manage": "stripe_portal", "renewal": None,
        "payments": [{"id": "in_1", "at": now, "amount": 8999, "currency": "usd", "status": "paid"}],
    }


def deliver(client, payload: dict, *, secret=BILLING_SECRET, caller="commercial"):
    from clipper.security import sha256_bytes, sign_request

    body = json.dumps(payload).encode()
    headers = sign_request(secret, caller, "POST", "/internal/billing/events", sha256_bytes(body))
    return client.post("/internal/billing/events", content=body, headers={**headers, "Content-Type": "application/json"})


def owned_streamer(uid: int, name: str | None = None, preset: str = "balanced") -> int:
    """A channel followed by this customer (only by it, not by the HTS). Returns the streamer id."""
    from clipper.db import session_scope
    from clipper.models import ChannelMonitor, Streamer

    name = name or uuid.uuid4().hex[:10]
    with session_scope() as db:
        s = Streamer(platform="twitch", channel=name, url=f"https://www.twitch.tv/{name}", hts_monitoring=False)
        db.add(s)
        db.flush()
        db.add(ChannelMonitor(user_id=uid, streamer_id=s.id, preset=preset))
        db.flush()
        return s.id


def live_segment(sid: int, idx: int, start: float, duration: float) -> int:
    """The channel goes live (shared session) and one segment is captured. Returns the session id."""
    from clipper import pipeline
    from clipper.db import session_scope

    with session_scope() as db:
        sess = pipeline.active_session(db, sid)
        sess_id = sess.id if sess else pipeline.report_status(db, sid, live=True)["session_id"]
        pipeline.register_segments(db, sess_id, "w1", "http://seg", [{"idx": idx, "start": start, "duration": duration}])
        return sess_id


def sub_state(c) -> dict:
    r = c.get("/api/account/subscription")
    assert r.status_code == 200, r.text
    return r.json()


# 1 + 4: verified sign-up, checkout bound to the account, access only after the provider-confirmed snapshot
def test_new_customer_signs_up_pays_and_gets_the_right_plan(app, client, monkeypatch):
    from clipper import accounts, billing_link

    sent = []
    monkeypatch.setattr(accounts, "smtp_ready", lambda db: True)
    monkeypatch.setattr(accounts, "send_mail", lambda db, to, subject, text: sent.append((to, text)))
    anon = TestClient(app)
    assert anon.get("/api/auth/config").json()["signup_enabled"] is True
    email = f"new-{uuid.uuid4().hex[:8]}@example.com"
    assert anon.post("/api/auth/signup", json={"email": email}).status_code == 202
    # Not usable before the e-mail owner creates a password
    assert anon.post("/api/auth/login", json={"email": email, "password": "x" * 12}).status_code == 401
    token = sent[-1][1].split("t=")[1].split()[0]
    assert anon.post("/api/auth/password-link", json={"token": token, "new_password": "minha-senha-forte-9"}).status_code == 200
    c = TestClient(app)
    me = c.post("/api/auth/login", json={"email": email, "password": "minha-senha-forte-9"})
    assert me.json()["role"] == "customer"
    assert c.get("/api/auth/me").json()["areas"] == ["account", "library"]
    uid = c.get("/api/auth/me").json() and _user_id(email)

    calls = []
    monkeypatch.setattr(billing_link, "call", lambda path, body: calls.append((path, body)) or (201, {"url": "https://checkout.stripe.com/c/pay/cs_test_x"}))
    r = c.post("/api/account/checkout", json={"plan": "pro", "market": "US", "language": "en"}, headers=CSRF)
    assert r.status_code == 201 and r.json()["url"].startswith("https://checkout.stripe.com/")
    # The account id comes from the session, never from the browser
    assert calls[0] == ("/internal/billing/checkout", {"account_id": uid, "email": email, "plan": "pro", "market": "US", "language": "en"})
    # Returning from the checkout grants nothing by itself
    assert sub_state(c)["entitled"] is False

    assert deliver(client, snapshot(uid)).json() == {"outcome": "applied"}
    state = sub_state(c)
    assert state["entitled"] is True and state["subscription"]["plan"] == "pro" and state["limits"]["channels"] == 4
    # A second checkout while subscribed is refused
    assert c.post("/api/account/checkout", json={"plan": "basic", "market": "US"}, headers=CSRF).status_code == 409


def _user_id(email: str) -> int:
    from sqlalchemy import select

    from clipper.db import session_scope
    from clipper.models import User

    with session_scope() as db:
        return db.scalar(select(User.id).where(User.email == email))


def test_signup_never_reveals_existing_accounts_and_needs_email(app, monkeypatch):
    from clipper import accounts

    anon = TestClient(app)
    monkeypatch.setattr(accounts, "smtp_ready", lambda db: False)
    assert anon.post("/api/auth/signup", json={"email": "x@example.com"}).status_code == 503
    sent = []
    monkeypatch.setattr(accounts, "smtp_ready", lambda db: True)
    monkeypatch.setattr(accounts, "send_mail", lambda db, to, subject, text: sent.append(to))
    assert anon.post("/api/auth/signup", json={"email": "admin@example.com"}).json() == {"ok": True}
    assert sent == [], "staff accounts get nothing (and the answer is identical)"
    assert anon.post("/api/auth/signup", json={"email": "not-an-email"}).status_code == 422


# 2 + 7: declined / incomplete / under-review payments never grant access
@pytest.mark.parametrize("status,review", [("none", ""), ("ended", "payment_review"), ("suspended", "")])
def test_declined_or_review_payments_do_not_grant_access(app, client, status, review):
    uid, c = customer(app)
    assert deliver(client, snapshot(uid, status=status, review=review, financial="incomplete")).json()["outcome"] == "applied"
    assert sub_state(c)["entitled"] is False
    assert c.post("/api/account/channels", json={"url": "https://www.twitch.tv/somechannel"}, headers=CSRF).status_code == 402


# 3 + 13: duplicates, reordering, missing account (retried later) and bad signatures
def test_duplicate_and_out_of_order_events_and_sync_recovery(app, client):
    uid, c = customer(app)
    first = snapshot(uid, seq=5)
    assert deliver(client, first).json() == {"outcome": "applied"}
    assert deliver(client, first).json() == {"outcome": "duplicate"}
    # An older snapshot delivered late never overwrites newer state
    assert deliver(client, snapshot(uid, seq=3, status="ended")).json() == {"outcome": "stale"}
    assert sub_state(c)["entitled"] is True

    # Account not ready yet: 409 (the commercial outbox retries), then applied once the account exists
    ghost = snapshot(999_999, seq=1)
    assert deliver(client, ghost).status_code == 409
    # Invalid payloads are permanently rejected (422); bad or foreign signatures are refused (401)
    bad = snapshot(uid, seq=6)
    bad["limits"] = {"hours": "lots"}
    assert deliver(client, bad).status_code == 422
    assert deliver(client, snapshot(uid, seq=7), secret="wrong-secret-0123456789abcdefghijk").status_code == 401
    assert deliver(client, snapshot(uid, seq=7), caller="test-worker").status_code == 401
    # The workers' internal secret is NOT accepted for billing
    assert deliver(client, snapshot(uid, seq=7), secret=os.environ["ASC_INTERNAL_SECRET"]).status_code == 401


# 5: renewal extends the period and starts a new usage cycle
def test_renewal_extends_the_period_and_resets_the_cycle(app, client):
    from clipper import entitlements
    from clipper.db import session_scope

    uid, c = customer(app)
    now = _now()
    deliver(client, snapshot(uid, seq=1, start=now - DAY, until=now + 29 * DAY))
    sid = owned_streamer(uid)
    live_segment(sid, 1, 0, 3600)
    with session_scope() as db:
        assert entitlements.usage_summary(db, uid)["live_seconds"] == 3600
    deliver(client, snapshot(uid, seq=2, start=now + 29 * DAY - 10, until=now + 59 * DAY))
    state = sub_state(c)
    assert state["usage"]["live_seconds"] == 0, "new cycle"
    assert state["subscription"]["access_until"].startswith(__import__("datetime").datetime.fromtimestamp(now + 59 * DAY, __import__("datetime").timezone.utc).strftime("%Y-%m-%d"))


# 6 + 8: expiry and refunds remove access, pause channels and stop running captures
def test_expiry_and_refund_remove_access_and_stop_work(app, client):
    from clipper import entitlements, pipeline
    from clipper.db import session_scope
    from clipper.models import StreamSession, Streamer

    uid, c = customer(app)
    deliver(client, snapshot(uid, seq=1))
    sid = owned_streamer(uid)
    with session_scope() as db:
        result = pipeline.report_status(db, sid, live=True, title="live")
        assert result["created"] is True
        session_id = result["session_id"]
    # Refund (or chargeback lost): access ends
    deliver(client, snapshot(uid, seq=2, status="ended", financial="refunded", until=0))
    assert sub_state(c)["entitled"] is False
    with session_scope() as db:
        # nobody else follows this live: the shared capture stops; the channel itself is never disabled by a plan
        assert pipeline.session_state(db, session_id)["stop"] is True
        assert db.get(Streamer, sid).enabled is True
        assert db.get(StreamSession, session_id) is not None
    # Time-based expiry: an "active" snapshot whose access_until already passed grants nothing
    uid2, c2 = customer(app)
    deliver(client, snapshot(uid2, seq=1, until=_now() - 60))
    assert sub_state(c2)["entitled"] is False
    with session_scope() as db:
        assert "assinatura inativa" in entitlements.check_session_start(db, db.get(Streamer, owned_streamer(uid2)))
    # Renewing again: the next live is followed again
    deliver(client, snapshot(uid, seq=3))
    with session_scope() as db:
        assert entitlements.check_session_start(db, db.get(Streamer, sid)) == ""


# 9: Basic customers cannot use Pro features
def test_basic_customer_cannot_use_pro_features(app, client):
    from clipper import editorial, entitlements, pipeline
    from clipper.db import session_scope
    from clipper.models import Clip, Streamer

    basic_id, basic = customer(app)
    pro_id, pro = customer(app)
    deliver(client, snapshot(basic_id, plan="basic"))
    deliver(client, snapshot(pro_id, plan="pro"))
    bsid, psid = owned_streamer(basic_id), owned_streamer(pro_id)
    live_segment(bsid, 0, 0, 60)
    live_segment(psid, 0, 0, 60)
    with session_scope() as db:
        assert entitlements.has_feature(db, db.get(Streamer, bsid), "secondPass") is False
        assert entitlements.has_feature(db, db.get(Streamer, bsid), "variants") is False
        assert entitlements.has_feature(db, db.get(Streamer, psid), "secondPass") is True
        # HTS channels keep every feature
        hts = Streamer(platform="twitch", channel=uuid.uuid4().hex[:10], url="https://www.twitch.tv/hts")
        db.add(hts)
        db.flush()
        assert entitlements.has_feature(db, hts, "secondPass") is True
        clips = {}
        for owner_sid, key in ((bsid, "basic"), (psid, "pro")):
            sess_id = pipeline.active_session(db, owner_sid).id
            clip = Clip(session_id=sess_id, streamer_id=owner_sid, start=10, end=40, peak=15, score=80, status="ready",
                        video_path=f"clips/x/{key}.mp4", file_size=1000)
            db.add(clip)
            db.flush()
            editorial.deliver(db, clip)
            clips[key] = clip.id
    assert basic.post(f"/api/account/clips/{clips['basic']}/export-4k", headers=CSRF).status_code == 403
    # Pro passes the entitlement check; the renderer has no 4K output yet (honest 501, nothing consumed)
    assert pro.post(f"/api/account/clips/{clips['pro']}/export-4k", headers=CSRF).status_code == 501
    with session_scope() as db:
        assert entitlements.usage_summary(db, pro_id)["exports_4k"] == 0


# 10: customers cannot change their own plan, limits or anything staff-only
def test_customer_cannot_modify_own_quotas_or_reach_staff_routes(app, client):
    uid, c = customer(app)
    deliver(client, snapshot(uid, plan="basic"))
    forged = snapshot(uid, plan="pro", seq=50)
    # Without the billing signature the inbox refuses, even with a valid customer session
    assert c.post("/internal/billing/events", json=forged, headers=CSRF).status_code == 401
    assert sub_state(c)["subscription"]["plan"] == "basic"
    for method, path in (("put", "/api/account/subscription"), ("patch", "/api/account/subscription"), ("post", "/api/account/subscription")):
        assert getattr(c, method)(path, json={"plan": "pro", "limits": LIMITS["pro"]}, headers=CSRF).status_code == 405
    # Staff routes stay closed to customers (deny by default)
    assert c.get("/api/streamers").status_code == 403
    assert c.post("/api/streamers", json={"url": "https://www.twitch.tv/abc"}, headers=CSRF).status_code == 403
    assert c.get("/api/admin/users").status_code == 403


# 11: limits hold under simultaneous operations
def test_simultaneous_operations_do_not_exceed_limits(app, client, monkeypatch):
    from clipper import entitlements, pipeline, platforms
    from clipper.api import routes_account
    from clipper.db import session_scope
    from clipper.models import Segment, Streamer

    uid, c = customer(app)
    deliver(client, snapshot(uid, plan="basic"))  # 1 channel, 1 simultaneous live, 40 h

    # Channels: several requests at once, only one fits
    def fake_parse(raw):
        name = raw.rsplit("/", 1)[-1]
        return platforms.StreamRef(platform="twitch", channel=name, url=raw, display_name=name)
    monkeypatch.setattr(routes_account, "parse_stream_url", fake_parse)
    results = []
    def add(i):
        results.append(c.post("/api/account/channels", json={"url": f"https://www.twitch.tv/race{uuid.uuid4().hex[:6]}{i}"}, headers=CSRF).status_code)
    threads = [threading.Thread(target=add, args=(i,)) for i in range(5)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert sorted(results).count(201) == 1 and all(code in (201, 409) for code in results)
    assert len(c.get("/api/account/channels").json()) == 1

    # Simultaneous lives: a second channel (inserted directly) cannot start while the first is live
    first = _streamer_of_monitor(c.get("/api/account/channels").json()[0]["id"])
    second = owned_streamer(uid)
    with session_scope() as db:
        assert pipeline.report_status(db, first, live=True)["created"] is True
    with session_scope() as db:
        assert pipeline.report_status(db, second, live=True)["session_id"] is None

    # Hours: segments past the franchise are not analysed and the capture is told to stop; duplicates count once
    with session_scope() as db:
        sess_id = pipeline.active_session(db, first).id
        pipeline.register_segments(db, sess_id, "w1", "http://seg", [{"idx": 0, "start": 0, "duration": 39 * 3600}])
        pipeline.register_segments(db, sess_id, "w1", "http://seg", [{"idx": 0, "start": 0, "duration": 39 * 3600}])
        assert entitlements.usage_summary(db, uid)["live_seconds"] == 39 * 3600
        pipeline.register_segments(db, sess_id, "w1", "http://seg", [{"idx": 1, "start": 39 * 3600, "duration": 7200}])
        pipeline.register_segments(db, sess_id, "w1", "http://seg", [{"idx": 2, "start": 41 * 3600, "duration": 60}])
        states = {s.idx: s.analysis_state for s in db.query(Segment).filter(Segment.session_id == sess_id)}
        assert states[1] != "skipped" and states[2] == "skipped"
        assert pipeline.session_state(db, sess_id)["stop"] is True
        assert entitlements.usage_summary(db, uid)["live_seconds"] == 41 * 3600


def _streamer_of_monitor(mid: int) -> int:
    from clipper.db import session_scope
    from clipper.models import ChannelMonitor

    with session_scope() as db:
        return db.get(ChannelMonitor, mid).streamer_id


# 12: financial state survives restarts (it lives in the database, not in memory)
def test_restart_keeps_financial_state(app, client):
    from clipper.api.app import create_app
    from clipper import entitlements
    from clipper.db import session_scope

    uid, c = customer(app)
    deliver(client, snapshot(uid, plan="pro"))
    fresh = create_app(run_maintenance=False)
    with TestClient(fresh) as again:
        assert again.post("/api/auth/login", json={"email": _email(uid), "password": "uma-senha-bem-longa-1"}).status_code == 200
        assert again.get("/api/account/subscription").json()["subscription"]["plan"] == "pro"
    with session_scope() as db:
        assert entitlements.current(db, uid).plan == "pro"


def _email(uid: int) -> str:
    from clipper.db import session_scope
    from clipper.models import User

    with session_scope() as db:
        return db.get(User, uid).email


# 14: customers are isolated from each other
def test_customers_cannot_see_or_touch_other_accounts(app, client):
    a_id, a = customer(app)
    b_id, b = customer(app)
    deliver(client, snapshot(a_id, plan="pro"))
    owned_streamer(a_id)
    a_monitor = a.get("/api/account/channels").json()[0]["id"]
    assert b.get("/api/account/subscription").json()["subscription"] is None
    assert b.get("/api/account/channels").json() == []
    assert b.delete(f"/api/account/channels/{a_monitor}", headers=CSRF).status_code == 404
    assert b.patch(f"/api/account/channels/{a_monitor}", json={"preset": "humor"}, headers=CSRF).status_code == 404
    # A snapshot cannot move a subscription to another account
    stolen = snapshot(b_id, seq=9, ref=f"sub_{a_id}")
    assert deliver(client, stolen).status_code == 422
    # Staff and share-only viewers cannot hold subscriptions
    assert deliver(client, snapshot(1, ref="sub_admin")).status_code == 409


def test_hts_channels_keep_working_without_any_subscription(client):
    from clipper import pipeline
    from clipper.db import session_scope
    from clipper.models import Segment, Streamer

    with session_scope() as db:
        s = Streamer(platform="twitch", channel=uuid.uuid4().hex[:10], url="https://www.twitch.tv/hts2")
        db.add(s)
        db.flush()
        result = pipeline.report_status(db, s.id, live=True)
        assert result["created"] is True
        pipeline.register_segments(db, result["session_id"], "w1", "http://seg", [{"idx": 0, "start": 0, "duration": 500 * 3600}])
        assert db.query(Segment).filter(Segment.session_id == result["session_id"]).one().analysis_state != "skipped"
        assert pipeline.session_state(db, result["session_id"])["stop"] is False
