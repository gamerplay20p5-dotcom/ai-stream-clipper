"""Automatic publishing for customers (PR #25): plans, destinations, daily TOTAL allowance, isolation, OAuth,
authorization, failures. Every platform API is simulated: no real account, token or post is ever involved."""

from __future__ import annotations

import threading
import uuid
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from conftest import CSRF
from test_billing_entitlements import DAY, _now, billing_env, customer, deliver  # noqa: F401


def limits(plan: str) -> dict:
    from clipper import catalog

    return catalog.limits_for(plan)


def snap(uid, seq=1, plan="creator", status="active", review="", until=None, start=None):
    now = _now()
    start = start if start is not None else now - DAY
    return {"event_id": uuid.uuid4().hex, "seq": seq, "provider": "stripe", "subscription_ref": f"sub_{uid}",
            "account_id": uid, "plan": plan, "market": "US", "financial_status": status, "review": review,
            "access": {"status": status, "until": until if until is not None else now + 30 * DAY, "period_start": start,
                       "period_end": now + 29 * DAY, "renews_at": None, "cancel_at_period_end": False},
            "limits": limits(plan), "manage": "stripe_portal", "renewal": None, "payments": []}


class FakePlatform:
    """Simulated YouTube API: OAuth, account info, upload and status. Records every call; never touches the network."""

    def __init__(self, platform="youtube", privacy=("private", "unlisted", "public")):
        self.platform, self.privacy = platform, list(privacy)
        self.uploads, self.fail_next, self.next_ids = [], [], []

    def auth_url(self, app, state, challenge):
        return f"https://accounts.example/auth?state={state}"

    def exchange(self, app, code, verifier):
        from clipper.publish.base import Tokens

        return Tokens(f"tok-{code}", "refresh", None, "scope")

    def refresh(self, app, tokens):
        return tokens

    def account_info(self, tokens):
        ids = self.next_ids.pop(0) if self.next_ids else [uuid.uuid4().hex[:12]]
        accounts = [{"external_id": i, "name": f"Canal {i[:4]}", "handle": i[:6],
                     "meta": {"privacy_options": self.privacy}} for i in ids]
        return {**accounts[0], "additional": accounts[1:]}

    def upload(self, pub, tokens, video, save_state):
        from clipper.publish.base import PublishError, UploadResult

        if self.fail_next:
            kind = self.fail_next.pop(0)
            raise PublishError("falha simulada", retryable=kind == "retry", auth=kind == "auth")
        self.uploads.append(pub.id)
        return UploadResult(f"vid{pub.id}", f"https://youtube.example/{pub.id}", {}, "published")

    def status(self, pub, tokens):
        return {"state": "published"}

    def metrics(self, pub, tokens):
        return {}


@pytest.fixture
def fake(monkeypatch, client):
    from clipper.db import session_scope
    from clipper.publish import service

    yt = FakePlatform()
    monkeypatch.setitem(service.PROVIDERS, "youtube", yt)
    with session_scope() as db:
        service.save_app(db, "youtube", "client-id", "client-secret", "https://clipper.example.test/api/publish/oauth/youtube/callback", "admin")
    return yt


@pytest.fixture
def channel(monkeypatch):
    from clipper import platforms
    from clipper.api import routes_account

    name = f"live{uuid.uuid4().hex[:8]}"
    monkeypatch.setattr(routes_account, "parse_stream_url", lambda raw: platforms.StreamRef(
        platform="twitch", channel=name, url=f"https://www.twitch.tv/{name}", display_name=name))
    return f"https://www.twitch.tv/{name}"


def subscriber(app, client, plan="creator", channel_url=None):
    uid, c = customer(app)
    assert deliver(client, snap(uid, plan=plan)).json()["outcome"] == "applied"
    sid = None
    if channel_url:
        r = c.post("/api/account/channels", json={"url": channel_url}, headers=CSRF)
        assert r.status_code == 201, r.text
        from clipper.db import session_scope
        from clipper.models import ChannelMonitor

        with session_scope() as db:
            sid = db.get(ChannelMonitor, r.json()["id"]).streamer_id
    return SimpleNamespace(uid=uid, c=c, sid=sid)


def connect(client, who, fake, ids=None):
    """The OWNER of the destination authorizes: start in its area, come back through the platform callback."""
    from clipper.db import session_scope
    from clipper.models import OAuthState

    if ids:
        fake.next_ids.append(ids)
    r = who.c.post("/api/account/publishing/connect/youtube", headers=CSRF)
    if r.status_code != 200:
        return r
    with session_scope() as db:
        state = db.scalar(select(OAuthState.state).where(OAuthState.owner_id == who.uid).order_by(OAuthState.created_at.desc()))
    return TestClient(client.app).get(f"/api/publish/oauth/youtube/callback?code=c{uuid.uuid4().hex[:6]}&state={state}",
                                      follow_redirects=False)


def enable(who, rights=True, approval=False, account=None, privacy="private", slots=("09:00", "12:00", "15:00", "18:00", "21:00")):
    assert who.c.put("/api/account/publishing/settings", json={"accept_terms": True, "enabled": True,
                                                              "timezone": "America/Sao_Paulo"}, headers=CSRF).status_code == 200
    accounts = who.c.get("/api/account/publishing").json()["accounts"]
    rules = []
    for acct in ([account] if account else accounts):
        r = who.c.post("/api/account/publishing/rules", json={
            "account_id": acct["id"], "streamer_id": who.sid, "require_approval": approval, "min_score": 50,
            "privacy": privacy, "slots": list(slots), "rights_confirmed": rights,
            "rights_note": "Sou editor autorizado do canal"}, headers=CSRF)
        assert r.status_code == 201, r.text
        rules.append(r.json())
    return rules


def live_clip(sid: int, *, start=100.0, score=80, sess=None):
    """A Short of the shared live, delivered to every follower's library (as the pipeline does)."""
    from clipper import pipeline, sharing, storage
    from clipper.db import session_scope
    from clipper.models import Clip

    with session_scope() as db:
        sess = sess or pipeline.active_session(db, sid)
        sess_id = sess.id if hasattr(sess, "id") else sess
        if sess_id is None:
            sess_id = pipeline.report_status(db, sid, live=True)["session_id"]
    with session_scope() as db:
        idx = int(start // 600)
        pipeline.register_segments(db, sess_id, "w1", "http://seg", [{"idx": idx, "start": idx * 600.0, "duration": 600.0}])
    with session_scope() as db:
        rel = f"clips/{uuid.uuid4().hex}/v1.mp4"
        path = storage.resolve(rel)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"fake-video")
        clip = Clip(session_id=sess_id, streamer_id=sid, start=start, end=start + 30, peak=start + 5, score=score,
                    status="ready", video_path=rel, file_size=10, sha256="", analysis_meta={}, vision_state="done")
        db.add(clip)
        db.flush()
        sharing.on_clip_status(db, clip)
        return clip.id


def tick():
    from clipper import customer_publish
    from clipper.db import session_scope

    with session_scope() as db:
        return customer_publish.tick(db)


def pubs(uid, status=None):
    from clipper.db import session_scope
    from clipper.models import Publication

    with session_scope() as db:
        q = select(Publication).where(Publication.owner_id == uid)
        if status:
            q = q.where(Publication.status == status)
        return [SimpleNamespace(id=p.id, status=p.status, account_id=p.account_id, clip_id=p.clip_id, error=p.last_error)
                for p in db.scalars(q.order_by(Publication.id))]


def make_due(ids):
    from clipper.db import session_scope, utcnow
    from clipper.models import Publication

    with session_scope() as db:
        for i in ids:
            p = db.get(Publication, i)
            p.status, p.scheduled_at = "scheduled", utcnow()


def run(pub_id):
    """The real worker path: queue job -> claim -> run_publish (with the simulated platform)."""
    from clipper import queue
    from clipper.db import session_scope
    from clipper.publish import runner

    with session_scope() as db:
        queue.enqueue(db, "publish", {"publication_id": pub_id}, idempotency_key=f"t:{pub_id}:{uuid.uuid4().hex}")
    with session_scope() as db:
        job = next(j for j in queue.claim(db, runner.worker_id(), ["publish"], has_gpu=False, limit=50)
                   if j.payload["publication_id"] == pub_id)
        job_id = job.id
    runner.run_publish(job_id, pub_id)


def units(uid):
    from clipper.db import session_scope
    from clipper.models import PublishUnit

    with session_scope() as db:
        return sorted((u.publication_id, u.state) for u in db.scalars(select(PublishUnit).where(PublishUnit.user_id == uid)))


# ----------------------------------------------------------------------------------------------------------------


def test_basic_only_sees_the_upgrade_offer(app, client, fake, channel):
    basic = subscriber(app, client, "basic", channel)
    body = basic.c.get("/api/account/publishing").json()
    assert body == {"plan": "basic", "allowed": False, "limits": {"destinations": 0, "daily": 0}, "upgrade": True}
    assert basic.c.post("/api/account/publishing/connect/youtube", headers=CSRF).status_code == 403
    assert basic.c.put("/api/account/publishing/settings", json={"enabled": True}, headers=CSRF).status_code == 403
    assert basic.c.post("/api/account/publishing/rules", json={"account_id": 1, "streamer_id": basic.sid},
                        headers=CSRF).status_code == 403


def test_destination_limits_creator_two_pro_four(app, client, fake):
    creator = subscriber(app, client, "creator")
    for _ in range(2):
        r = connect(client, creator, fake)
        assert r.status_code == 303 and r.headers["location"].endswith("/conta/publicacao?conectado=1")
    third = connect(client, creator, fake)
    assert third.status_code == 409 and "2 destino" in third.json()["detail"]
    pro = subscriber(app, client, "pro")
    # Several accounts shared in one authorization: only the free slots are kept
    r = connect(client, pro, fake, ids=[uuid.uuid4().hex[:10] for _ in range(5)])
    assert "erro=" not in r.headers["location"]
    assert len(pro.c.get("/api/account/publishing").json()["accounts"]) == 4
    assert connect(client, pro, fake).status_code == 409
    # Destinations are accounts, not platforms: all four are YouTube here, no fictitious fourth network
    assert {a["platform"] for a in pro.c.get("/api/account/publishing").json()["accounts"]} == {"youtube"}


def test_daily_total_is_shared_by_all_destinations(app, client, fake, channel):
    from clipper import customer_publish
    from clipper.db import session_scope
    from clipper.models import Publication

    creator = subscriber(app, client, "creator", channel)
    connect(client, creator, fake)
    connect(client, creator, fake)
    enable(creator)
    for start in (100, 700):
        live_clip(creator.sid, start=start)
    tick()
    created = pubs(creator.uid)
    assert len(created) == 4, "2 Shorts x 2 destinations"
    assert len({p.account_id for p in created}) == 2
    make_due([p.id for p in created])
    verdicts = []
    with session_scope() as db:
        for p in created:
            verdicts.append(customer_publish.before_upload(db, db.get(Publication, p.id))[0])
    assert verdicts == ["go", "go", "go", "defer"], "Creator: 3 per day in TOTAL"
    with session_scope() as db:
        assert customer_publish.summary(db, creator.uid)["remaining"] == 0


def test_pro_daily_four_and_the_same_short_on_two_destinations_counts_twice(app, client, fake, channel):
    from clipper import customer_publish
    from clipper.db import session_scope
    from clipper.models import Publication

    pro = subscriber(app, client, "pro", channel)
    for _ in range(3):
        connect(client, pro, fake)
    enable(pro)
    for start in (100, 700):
        live_clip(pro.sid, start=start)
    tick()
    created = pubs(pro.uid)
    assert len(created) == 6
    make_due([p.id for p in created])
    with session_scope() as db:
        got = [customer_publish.before_upload(db, db.get(Publication, p.id))[0] for p in created]
    assert got.count("go") == 4 and got.count("defer") == 2
    by_clip = {}
    for p, v in zip(created, got):
        if v == "go":
            by_clip.setdefault(p.clip_id, 0)
            by_clip[p.clip_id] += 1
    assert max(by_clip.values()) >= 2, "one Short to two destinations used two units"


def test_two_workers_racing_for_the_last_slot(app, client, fake, channel):
    from clipper import customer_publish
    from clipper.db import session_scope
    from clipper.models import Publication

    creator = subscriber(app, client, "creator", channel)
    connect(client, creator, fake)
    connect(client, creator, fake)
    enable(creator)
    for start in (100, 700):
        live_clip(creator.sid, start=start)
    tick()
    created = pubs(creator.uid)
    make_due([p.id for p in created])
    with session_scope() as db:
        for p in created[:2]:
            assert customer_publish.before_upload(db, db.get(Publication, p.id))[0] == "go"
    results = []
    barrier = threading.Barrier(2)

    def race(pid):
        barrier.wait()
        with session_scope() as db:
            results.append(customer_publish.before_upload(db, db.get(Publication, pid))[0])
    threads = [threading.Thread(target=race, args=(p.id,)) for p in created[2:4]]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert sorted(results) == ["defer", "go"]
    assert [s for _, s in units(creator.uid)].count("reserved") == 3


def test_retry_and_restart_never_count_twice_or_repost(app, client, fake, channel):
    creator = subscriber(app, client, "creator", channel)
    connect(client, creator, fake)
    enable(creator)
    live_clip(creator.sid)
    tick()
    [p] = pubs(creator.uid)
    make_due([p.id])
    fake.fail_next = ["retry"]
    run(p.id)  # network failure: back to the queue, the unit stays held
    assert pubs(creator.uid)[0].status == "scheduled"
    assert units(creator.uid) == [(p.id, "reserved")]
    make_due([p.id])
    run(p.id)  # retry succeeds: still one unit
    assert pubs(creator.uid)[0].status == "published"
    assert units(creator.uid) == [(p.id, "consumed")]
    run(p.id)  # worker restart / duplicate job: a confirmed publication is never sent again
    assert fake.uploads == [p.id]
    assert units(creator.uid) == [(p.id, "consumed")]


def test_permanent_failure_and_cancellation_give_the_slot_back(app, client, fake, channel):
    creator = subscriber(app, client, "creator", channel)
    connect(client, creator, fake)
    enable(creator)
    live_clip(creator.sid, start=100)
    live_clip(creator.sid, start=700)
    tick()
    first, second = pubs(creator.uid)
    make_due([first.id])
    fake.fail_next = ["permanent"]
    run(first.id)
    assert pubs(creator.uid)[0].status == "failed"
    assert dict(units(creator.uid))[first.id] == "released", "nothing was published"
    assert creator.c.post(f"/api/account/publishing/publications/{second.id}/cancel", headers=CSRF).status_code == 200
    assert pubs(creator.uid)[1].status == "cancelled"


def test_two_customers_on_the_same_live_publish_to_their_own_destinations(app, client, fake, channel):
    a = subscriber(app, client, "creator", channel)
    b = subscriber(app, client, "pro", channel)
    assert a.sid == b.sid, "same shared channel"
    connect(client, a, fake)
    connect(client, b, fake)
    enable(a)
    enable(b)
    live_clip(a.sid)
    tick()
    pa, pb = pubs(a.uid), pubs(b.uid)
    assert len(pa) == 1 and len(pb) == 1 and pa[0].clip_id == pb[0].clip_id
    a_accounts = {x["id"] for x in a.c.get("/api/account/publishing").json()["accounts"]}
    b_accounts = {x["id"] for x in b.c.get("/api/account/publishing").json()["accounts"]}
    assert pa[0].account_id in a_accounts and pb[0].account_id in b_accounts and not a_accounts & b_accounts
    make_due([pa[0].id, pb[0].id])
    run(pa[0].id)
    run(pb[0].id)
    assert units(a.uid) == [(pa[0].id, "consumed")] and units(b.uid) == [(pb[0].id, "consumed")]


def test_customers_cannot_reach_each_other_or_the_hts_panel(app, client, fake, channel):
    a = subscriber(app, client, "pro", channel)
    b = subscriber(app, client, "pro", channel)
    connect(client, a, fake)
    [rule] = enable(a)
    live_clip(a.sid)
    tick()
    [pub] = pubs(a.uid)
    acct = a.c.get("/api/account/publishing").json()["accounts"][0]["id"]
    body = b.c.get("/api/account/publishing").json()
    assert body["accounts"] == [] and body["rules"] == [] and body["upcoming"] == []
    for method, path, payload in [
        ("delete", f"/api/account/publishing/accounts/{acct}", None),
        ("put", f"/api/account/publishing/rules/{rule['id']}", {"account_id": acct, "streamer_id": a.sid}),
        ("delete", f"/api/account/publishing/rules/{rule['id']}", None),
        ("post", f"/api/account/publishing/publications/{pub.id}/approve", None),
        ("post", f"/api/account/publishing/publications/{pub.id}/cancel", None),
    ]:
        r = getattr(b.c, method)(path, headers=CSRF, **({"json": payload} if payload else {}))
        assert r.status_code == 404, (path, r.status_code)
    # B cannot point a rule at A's destination either
    r = b.c.post("/api/account/publishing/rules", json={"account_id": acct, "streamer_id": b.sid, "rights_confirmed": True},
                 headers=CSRF)
    assert r.status_code == 404
    # No token, secret or scope ever leaves the API
    assert "tok-" not in a.c.get("/api/account/publishing").text and "tokens" not in a.c.get("/api/account/publishing").text
    # The HTS publishing panel is staff-only and never lists customer resources
    assert a.c.get("/api/publish/overview").status_code == 403
    admin = TestClient(app)
    admin.post("/api/auth/login", json={"email": "admin@example.com", "password": "correct-horse-battery"})
    overview = admin.get("/api/publish/overview").json()
    assert all(x["id"] != acct for x in overview["accounts"])
    assert admin.delete(f"/api/publish/accounts/{acct}", headers=CSRF).status_code == 404
    assert all(p["id"] != pub.id for p in admin.get("/api/publish/publications").json())


def test_subscription_changes_cancel_suspend_expire_downgrade_upgrade_review(app, client, fake, channel):
    from clipper import customer_publish
    from clipper.db import session_scope
    from clipper.models import Publication

    pro = subscriber(app, client, "pro", channel)
    for _ in range(4):
        connect(client, pro, fake)
    enable(pro)
    live_clip(pro.sid)
    tick()

    def gate():
        with session_scope() as db:
            return customer_publish.gate(db, pro.uid)

    assert gate() == ("ok", "")
    deliver(client, snap(pro.uid, seq=2, plan="pro", review="dispute"))
    assert gate()[0] == "hold", "financial review: uploads wait (PR #24 policy)"
    deliver(client, snap(pro.uid, seq=3, plan="creator"))  # downgrade: 2 destinations
    accounts = pro.c.get("/api/account/publishing").json()["accounts"]
    assert [a["paused_reason"] for a in accounts].count("") == 2
    with session_scope() as db:
        paused = [p for p in db.scalars(select(Publication).where(Publication.owner_id == pro.uid))
                  if db.get(__import__("clipper.models", fromlist=["PublishAccount"]).PublishAccount, p.account_id).paused_reason]
        assert paused and all(customer_publish.before_upload(db, p)[0] == "refuse" for p in paused)
    deliver(client, snap(pro.uid, seq=4, plan="pro"))  # upgrade back
    assert all(a["paused_reason"] == "" for a in pro.c.get("/api/account/publishing").json()["accounts"])
    deliver(client, snap(pro.uid, seq=5, plan="pro", status="suspended"))
    assert gate()[0] == "off"
    deliver(client, snap(pro.uid, seq=6, plan="pro", status="ended", until=_now() - 1))  # canceled / expired
    assert gate() == ("off", "assinatura inativa")
    make_due([p.id for p in pubs(pro.uid)][:1])
    run(pubs(pro.uid)[0].id)
    assert pubs(pro.uid)[0].status == "waiting" and fake.uploads == [], "no new upload without access"


def test_expired_or_disconnected_destination(app, client, fake, channel):
    from clipper.db import session_scope
    from clipper.models import PublishAccount

    creator = subscriber(app, client, "creator", channel)
    connect(client, creator, fake)
    enable(creator)
    live_clip(creator.sid, start=100)
    live_clip(creator.sid, start=700)
    tick()
    first, second = pubs(creator.uid)
    acct_id = first.account_id
    with session_scope() as db:
        db.get(PublishAccount, acct_id).status = "expired"
    make_due([first.id])
    run(first.id)
    assert pubs(creator.uid)[0].status == "failed" and "conecte de novo" in pubs(creator.uid)[0].error
    assert creator.c.get("/api/account/publishing").json()["accounts"][0]["needs_reconnect"] is True
    r = creator.c.delete(f"/api/account/publishing/accounts/{acct_id}", headers=CSRF)
    assert r.status_code == 200
    assert pubs(creator.uid)[1].status == "cancelled"
    with session_scope() as db:
        assert db.get(PublishAccount, acct_id).tokens_enc == ""


def test_paused_automation_and_terms(app, client, fake, channel):
    creator = subscriber(app, client, "creator", channel)
    connect(client, creator, fake)
    # Off by default; cannot be turned on without accepting the publishing authorization
    assert creator.c.get("/api/account/publishing").json()["enabled"] is False
    assert creator.c.put("/api/account/publishing/settings", json={"enabled": True}, headers=CSRF).status_code == 409
    enable(creator)
    assert creator.c.put("/api/account/publishing/settings", json={"enabled": False}, headers=CSRF).status_code == 200
    live_clip(creator.sid)
    tick()
    assert pubs(creator.uid) == [], "paused: nothing is queued"
    creator.c.put("/api/account/publishing/settings", json={"enabled": True}, headers=CSRF)
    tick()
    assert len(pubs(creator.uid)) == 1


def test_republication_rights_and_hts_block(app, client, fake, channel):
    creator = subscriber(app, client, "creator", channel)
    connect(client, creator, fake)
    creator.c.put("/api/account/publishing/settings", json={"accept_terms": True, "enabled": True}, headers=CSRF)
    acct = creator.c.get("/api/account/publishing").json()["accounts"][0]["id"]
    r = creator.c.post("/api/account/publishing/rules", json={"account_id": acct, "streamer_id": creator.sid,
                                                             "rights_confirmed": False}, headers=CSRF)
    assert r.status_code == 409 and "direito" in r.json()["detail"]
    enable(creator, account={"id": acct})
    live_clip(creator.sid)
    tick()
    [p] = pubs(creator.uid)
    admin = TestClient(app)
    admin.post("/api/auth/login", json={"email": "admin@example.com", "password": "correct-horse-battery"})
    r = admin.patch(f"/api/admin/channels/{creator.sid}", json={"customer_publish_blocked": True,
                                                                "reason": "pedido do criador"}, headers=CSRF)
    assert r.status_code == 200
    assert pubs(creator.uid)[0].status == "cancelled"


def test_incompatible_privacy_is_refused(app, client, fake, channel):
    fake.privacy = ["public"]  # e.g. a Reels-only destination
    creator = subscriber(app, client, "creator", channel)
    connect(client, creator, fake)
    creator.c.put("/api/account/publishing/settings", json={"accept_terms": True}, headers=CSRF)
    acct = creator.c.get("/api/account/publishing").json()["accounts"][0]["id"]
    r = creator.c.post("/api/account/publishing/rules", json={"account_id": acct, "streamer_id": creator.sid,
                                                             "privacy": "private", "rights_confirmed": True}, headers=CSRF)
    assert r.status_code == 422 and "privacidade" in r.json()["detail"]


def test_over_the_daily_limit_shorts_wait_for_the_next_days(app, client, fake, channel):
    from clipper import customer_publish
    from clipper.db import session_scope, utcnow

    creator = subscriber(app, client, "creator", channel)
    connect(client, creator, fake)
    enable(creator)
    for i in range(7):
        live_clip(creator.sid, start=100 + 600 * i)
    tick()
    created = pubs(creator.uid)
    assert len(created) == 7
    with session_scope() as db:
        from clipper.models import Publication

        days = {}
        for p in created:
            row = db.get(Publication, p.id)
            assert row.status == "scheduled"
            d = customer_publish.local_day("America/Sao_Paulo", row.scheduled_at)
            days[d] = days.get(d, 0) + 1
        assert max(days.values()) <= 3 and len(days) >= 3, days
        assert all(row.scheduled_at > utcnow() for row in db.scalars(select(Publication).where(Publication.owner_id == creator.uid)))


def test_manual_approval_and_timezone(app, client, fake, channel):
    creator = subscriber(app, client, "creator", channel)
    connect(client, creator, fake)
    assert creator.c.put("/api/account/publishing/settings", json={"timezone": "Mars/Base"}, headers=CSRF).status_code == 422
    enable(creator, approval=True)
    live_clip(creator.sid)
    tick()
    [p] = pubs(creator.uid)
    assert p.status == "proposed"
    assert creator.c.post(f"/api/account/publishing/publications/{p.id}/approve", headers=CSRF).status_code == 200
    assert pubs(creator.uid)[0].status == "scheduled"


def test_local_day_is_consistent_across_dst():
    from datetime import datetime

    from clipper import customer_publish

    # New York: 2026-11-01 02:00 EDT -> 01:00 EST. 05:30 UTC and 06:30 UTC are both Nov 1 locally
    assert customer_publish.local_day("America/New_York", datetime(2026, 11, 1, 5, 30)) == "2026-11-01"
    assert customer_publish.local_day("America/New_York", datetime(2026, 11, 1, 6, 30)) == "2026-11-01"
    assert customer_publish.local_day("America/New_York", datetime(2026, 11, 2, 4, 59)) == "2026-11-01"
    assert customer_publish.local_day("America/New_York", datetime(2026, 11, 2, 5, 0)) == "2026-11-02"


def test_courtesy_plans_are_separate_and_audited(app, client):
    from clipper import entitlements
    from clipper.db import session_scope
    from clipper.models import AccountSubscription, AuditLog

    basic = subscriber(app, client, "basic")
    admin = TestClient(app)
    admin.post("/api/auth/login", json={"email": "admin@example.com", "password": "correct-horse-battery"})
    assert basic.c.post(f"/api/admin/customers/{basic.uid}/grants", json={"plan": "pro", "days": 7, "reason": "teste"},
                        headers=CSRF).status_code == 403
    r = admin.post(f"/api/admin/customers/{basic.uid}/grants", json={"plan": "creator", "days": 14,
                                                                     "reason": "parceria de lançamento"}, headers=CSRF)
    assert r.status_code == 201, r.text
    grant = r.json()
    with session_scope() as db:
        ent = entitlements.current(db, basic.uid)
        assert (ent.plan, ent.provider) == ("creator", "hts"), "higher plan wins"
        paid = db.scalar(select(AccountSubscription).where(AccountSubscription.user_id == basic.uid,
                                                           AccountSubscription.provider == "stripe"))
        assert paid.plan == "basic" and paid.financial_status == "active", "the paid subscription is untouched"
    # The customer can still buy a plan while on courtesy
    detail = admin.get(f"/api/admin/customers/{basic.uid}").json()
    assert detail["plan"]["courtesy"] is True and "tokens" not in str(detail)
    r = admin.post(f"/api/admin/customers/{basic.uid}/grants/{grant['id']}/revoke", json={"reason": "fim do teste"},
                   headers=CSRF)
    assert r.status_code == 200 and r.json()["active"] is False
    with session_scope() as db:
        assert entitlements.current(db, basic.uid).plan == "basic"
        actions = {a.action for a in db.scalars(select(AuditLog).where(AuditLog.target == f"user:{basic.uid}"))}
        assert {"admin.courtesy_grant", "admin.courtesy_revoke"} <= actions
    # Same plan: the paid subscription keeps precedence over a courtesy
    admin.post(f"/api/admin/customers/{basic.uid}/grants", json={"plan": "basic", "days": 30, "reason": "compensação"},
               headers=CSRF)
    with session_scope() as db:
        assert entitlements.current(db, basic.uid).provider == "stripe"


def test_hts_admin_panel(app, client, fake, channel):
    a = subscriber(app, client, "creator", channel)
    b = subscriber(app, client, "pro", channel)
    connect(client, a, fake)
    admin = TestClient(app)
    admin.post("/api/auth/login", json={"email": "admin@example.com", "password": "correct-horse-battery"})
    assert a.c.get("/api/admin/customers").status_code == 403
    rows = {r["id"]: r for r in admin.get("/api/admin/customers").json()}
    assert rows[a.uid]["plan"]["plan"] == "creator" and rows[a.uid]["destinations"] == 1
    detail = admin.get(f"/api/admin/customers/{a.uid}").json()
    assert detail["destinations"][0]["platform"] == "youtube"
    assert "tok-" not in str(detail) and "tokens_enc" not in str(detail)
    channels = {c["id"]: c for c in admin.get("/api/admin/channels").json()}
    assert channels[a.sid]["followers"] == 2 and channels[a.sid]["hts_monitoring"] is False
    assert admin.patch(f"/api/admin/channels/{a.sid}", json={"hts_monitoring": True}, headers=CSRF).status_code == 200
    assert admin.post(f"/api/admin/customers/{a.uid}/publishing/block", json={"reason": "denúncia em análise"},
                      headers=CSRF).status_code == 200
    assert a.c.put("/api/account/publishing/settings", json={"enabled": True}, headers=CSRF).status_code == 403
    cap = admin.get("/api/admin/capacity").json()
    assert cap["customers"] >= 2 and "jobs_pending" in cap
    assert admin.get("/api/admin/customer-publish-platforms").json()["enabled"] == ["youtube"]
    r = admin.put("/api/admin/customer-publish-platforms", json={"platforms": ["youtube", "tiktok", "facebook"]}, headers=CSRF)
    assert r.json()["enabled"] == ["youtube", "facebook"], "TikTok never automatic"
    admin.put("/api/admin/customer-publish-platforms", json={"platforms": ["youtube"]}, headers=CSRF)
    _ = b


def test_platforms_not_homologated_are_not_offered(app, client, fake):
    creator = subscriber(app, client, "creator")
    for platform in ("instagram", "tiktok", "facebook"):
        r = creator.c.post(f"/api/account/publishing/connect/{platform}", headers=CSRF)
        assert r.status_code == 409 and "ainda não está disponível" in r.json()["detail"]
    assert [p["id"] for p in creator.c.get("/api/account/publishing").json()["platforms"]] == ["youtube"]
