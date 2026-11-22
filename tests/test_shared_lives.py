"""Shared lives: one capture and one base analysis per live, however many customers follow the channel.

Ten customers (Basic, Creator and Pro, with different editorial presets) follow the same streamer. The suite checks
that the expensive work happens once, that each customer has its own preset, quota and library, that one customer
running out or being suspended never stops the others, that media is not duplicated or deleted while someone still
needs it, and that nothing leaks between accounts.
"""

from __future__ import annotations

import threading
import uuid

import pytest
from sqlalchemy import func, select

from conftest import CSRF
from test_billing_entitlements import LIMITS, billing_env, customer, deliver, snapshot  # noqa: F401

CREATOR = {"hours": 100, "channels": 2, "simultaneous": 2, "storage_gb": 12, "export_4k": 0,
           "features": {"ai": "enhanced", "secondPass": False, "variants": False, "priority": False, "editing": True}}
PLANS = {"basic": LIMITS["basic"], "creator": CREATOR, "pro": LIMITS["pro"]}


@pytest.fixture
def channel_url(monkeypatch):
    from clipper import platforms
    from clipper.api import routes_account

    name = f"alan{uuid.uuid4().hex[:8]}"

    def fake_parse(raw):
        n = raw.rstrip("/").rsplit("/", 1)[-1]
        return platforms.StreamRef(platform="twitch", channel=n, url=f"https://www.twitch.tv/{n}", display_name=n)

    monkeypatch.setattr(routes_account, "parse_stream_url", fake_parse)
    return f"https://www.twitch.tv/{name}"


def subscriber(app, client, plan: str, *, hours=None, seq=1):
    uid, c = customer(app)
    snap = snapshot(uid, plan="pro" if plan == "pro" else "basic", seq=seq)
    snap["plan"] = plan
    snap["limits"] = {**PLANS[plan], **({"hours": hours} if hours is not None else {})}
    assert deliver(client, snap).json()["outcome"] == "applied"
    return uid, c


@pytest.fixture
def ten(app, client, channel_url):
    """10 customers on the same channel: 4 Basic, 3 Creator (humor, intense, best), 3 Pro (humor, highlights, balanced).
    The first Basic has a 1-hour franchise so it runs out during the live."""
    people = []
    layout = [("basic", "balanced", 1), ("basic", "balanced", None), ("basic", "balanced", None), ("basic", "balanced", None),
              ("creator", "humor", None), ("creator", "intense", None), ("creator", "best", None),
              ("pro", "humor", None), ("pro", "highlights", None), ("pro", "balanced", None)]
    for plan, preset, hours in layout:
        uid, c = subscriber(app, client, plan, hours=hours)
        r = c.post("/api/account/channels", json={"url": channel_url, "preset": preset}, headers=CSRF)
        assert r.status_code == 201, r.text
        people.append({"uid": uid, "c": c, "plan": plan, "preset": preset, "monitor": r.json()["id"]})
    return people


def _streamer_id(url: str) -> int:
    from clipper.db import session_scope
    from clipper.models import Streamer

    with session_scope() as db:
        return db.scalar(select(Streamer.id).where(Streamer.channel == url.rsplit("/", 1)[-1]))


def test_ten_customers_share_one_capture_and_one_base_analysis(ten, channel_url):
    from clipper import entitlements, pipeline
    from clipper.db import session_scope
    from clipper.models import Job, MonitorSession, Segment, Streamer, UsageEntry

    with session_scope() as db:
        assert db.scalar(select(func.count()).select_from(Streamer).where(
            Streamer.channel == channel_url.rsplit("/", 1)[-1])) == 1, "one source channel"
    sid = _streamer_id(channel_url)

    # The monitor reports the live several times, also concurrently: one session, one capture job
    def report():
        with session_scope() as db:
            pipeline.report_status(db, sid, live=True, title="live")
    threads = [threading.Thread(target=report) for _ in range(5)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    report()
    with session_scope() as db:
        sess = pipeline.active_session(db, sid)
        sessions = db.scalar(select(func.count()).select_from(type(sess)).where(type(sess).streamer_id == sid))
        assert sessions == 1
        captures = [j for j in db.scalars(select(Job).where(Job.type == "capture")) if (j.payload or {}).get("streamer_id") == sid]
        assert len(captures) == 1
        assert len(entitlements.attachments(db, sess.id, "watching")) == 10
        sess_id, profile = sess.id, dict(sess.base_profile)

    # Three segments (one hour in total), the second one delivered twice (reconnect): base analysis once per segment
    for idx in range(3):
        with session_scope() as db:
            pipeline.register_segments(db, sess_id, "w1", "http://seg", [{"idx": idx, "start": idx * 1200.0, "duration": 1200.0}])
    with session_scope() as db:
        pipeline.register_segments(db, sess_id, "w1", "http://seg", [{"idx": 1, "start": 1200.0, "duration": 1200.0, "run_id": 1}])
        audio = [j for j in db.scalars(select(Job).where(Job.type == "audio_analyze")) if (j.payload or {}).get("session_id") == sess_id]
        assert len(audio) == 3, "one base transcription per segment, not one per customer"
        assert all(j.payload["whisper_model"] == profile["whisper_model"] and j.payload["language"] == profile["language"] for j in audio)
        assert db.scalar(select(func.count()).select_from(Segment).where(Segment.session_id == sess_id)) == 3
        # Each customer is charged once per segment it followed: 10 customers x 3 segments, no double count
        rows = db.scalars(select(UsageEntry).where(UsageEntry.source_key.like(f"live:%:{sess_id}:%"))).all()
        assert len(rows) == 30
        for p in ten:
            assert sum(r.amount for r in rows if r.user_id == p["uid"]) == 3600
        # The 1-hour Basic ran out at the end of the hour; the other nine keep following
        first = db.scalar(select(MonitorSession).where(MonitorSession.user_id == ten[0]["uid"], MonitorSession.session_id == sess_id))
        assert first.state == "stopped" and first.until_t == 3600.0 and "franquia" in first.stop_reason
        assert len(entitlements.attachments(db, sess_id, "watching")) == 9
        assert pipeline.session_state(db, sess_id)["stop"] is False

        # Next segment: not charged to the customer that ran out
        pipeline.register_segments(db, sess_id, "w1", "http://seg", [{"idx": 3, "start": 3600.0, "duration": 600.0}])
        assert entitlements.usage_summary(db, ten[0]["uid"])["live_seconds"] == 3600
        assert entitlements.usage_summary(db, ten[1]["uid"])["live_seconds"] == 4200


def test_presets_libraries_and_media_are_per_customer(app, client, ten, channel_url):
    from clipper import entitlements, pipeline, sharing, storage_lifecycle
    from clipper.db import session_scope
    from clipper.models import Clip, ClipShare

    sid = _streamer_id(channel_url)
    with session_scope() as db:
        sess_id = pipeline.report_status(db, sid, live=True)["session_id"]
    for idx in range(4):
        with session_scope() as db:
            pipeline.register_segments(db, sess_id, "w1", "http://seg", [{"idx": idx, "start": idx * 1200.0, "duration": 1200.0}])

    # Customer #2 (Basic) is suspended mid-live: only it stops; the shared capture goes on
    suspended = ten[1]
    deliver(client, {**snapshot(suspended["uid"], plan="basic", seq=2, status="suspended"), "limits": LIMITS["basic"]})
    with session_scope() as db:
        assert pipeline.session_state(db, sess_id)["stop"] is False
        assert "assinatura inativa" in db.scalar(select(entitlements.MonitorSession.stop_reason).where(
            entitlements.MonitorSession.user_id == suspended["uid"], entitlements.MonitorSession.session_id == sess_id))

    # One funny moment after the first Basic ran out (t = 4000 s): one shared render, one physical file
    with session_scope() as db:
        clip = Clip(session_id=sess_id, streamer_id=sid, start=4000, end=4030, peak=4010, score=60, status="ready",
                    video_path=f"clips/{uuid.uuid4().hex}/v1.mp4", file_size=5_000_000,
                    score_breakdown={"intensity": 2, "surprise": 2, "humor": 9, "reaction": 8, "audience": 8, "uniqueness": 5})
        db.add(clip)
        db.flush()
        sharing.on_clip_status(db, clip)
        sharing.on_clip_status(db, clip)  # idempotent
        clip_id, path = clip.id, clip.video_path
        shares = db.scalars(select(ClipShare).where(ClipShare.clip_id == clip_id, ClipShare.revoked_at.is_(None))).all()
        got = {s.user_id for s in shares}
        assert all(s.video_path == path for s in shares), "same physical file for everyone"
        assert len(shares) == len(got), "one delivery per customer"
        # Protected from cleanup while any customer holds it
        assert any("compartilhado" in why for why in storage_lifecycle.protection(db, clip))

    by = {i: p["uid"] for i, p in enumerate(ten)}
    expected = {
        0: False,  # ran out of hours before this moment
        1: True,  # suspended AFTER this moment: it was followed and paid while access was valid
        2: True, 3: True,  # Basic: base selection (score 60 >= 55)
        4: True,  # Creator humor
        5: False,  # Creator intense: low intensity
        6: False,  # Creator best: 60 < 65
        7: True,  # Pro humor
        8: False,  # Pro highlights: low surprise and intensity
        9: True,  # Pro balanced
    }
    assert {i for i, want in expected.items() if want} == {i for i, uid in by.items() if uid in got}

    # Each library shows only its own Shorts; a customer without the clip gets 404 for it
    for i, p in enumerate(ten):
        items = p["c"].get("/api/library").json()["items"]
        assert [x["clip_id"] for x in items] == ([clip_id] if expected[i] else [])
        status = p["c"].get(f"/api/library/{clip_id}").status_code
        assert (status == 200) == expected[i]
    # Storage counts in each library that holds the file
    with session_scope() as db:
        assert entitlements.storage_bytes(db, ten[2]["uid"]) == 5_000_000
        assert entitlements.storage_bytes(db, ten[0]["uid"]) == 0

    # A customer that leaves the channel keeps its library; the others and the capture are untouched
    leaver = ten[9]
    assert leaver["c"].delete(f"/api/account/channels/{leaver['monitor']}", headers=CSRF).status_code == 200
    assert [x["clip_id"] for x in leaver["c"].get("/api/library").json()["items"]] == [clip_id]
    with session_scope() as db:
        assert pipeline.session_state(db, sess_id)["stop"] is False
        assert len(entitlements.attachments(db, sess_id, "watching")) == 7

    # The HTS editorial rejection does not take a customer's delivery away; a failed render does
    with session_scope() as db:
        c = db.get(Clip, clip_id)
        c.status = "rejected"
        sharing.on_clip_status(db, c)
        db.flush()
        assert len(sharing.active_shares_of_clip(db, clip_id)) == len(got)
        c.status = "failed"
        sharing.on_clip_status(db, c)
        db.flush()
        assert sharing.active_shares_of_clip(db, clip_id) == []


def test_customer_views_never_expose_the_shared_pipeline(ten, channel_url):
    from clipper import pipeline
    from clipper.db import session_scope

    sid = _streamer_id(channel_url)
    with session_scope() as db:
        pipeline.report_status(db, sid, live=True)
    allowed = {"id", "platform", "channel", "url", "display_name", "status", "clips", "last_live_at", "preset", "monitoring"}
    for p in ten:
        [ch] = p["c"].get("/api/account/channels").json()
        assert set(ch) == allowed
        assert set(ch["monitoring"]) == {"state", "note"}
        assert ch["id"] == p["monitor"]
        body = p["c"].get("/api/account/channels").text
        for other in ten:
            if other is not p:
                assert f'"id":{other["monitor"]},' not in body.replace(" ", "")
        # Another customer's monitor is invisible and untouchable
        other = ten[(ten.index(p) + 1) % len(ten)]
        assert p["c"].patch(f"/api/account/channels/{other['monitor']}", json={"preset": "balanced"}, headers=CSRF).status_code == 404
        assert p["c"].delete(f"/api/account/channels/{other['monitor']}", headers=CSRF).status_code == 404
        # Internal pipeline routes stay closed
        assert p["c"].get("/api/streamers").status_code == 403
        assert p["c"].get("/api/jobs").status_code in (403, 404)


def test_presets_follow_the_plan_and_never_change_quotas(ten):
    basic, creator = ten[2], ten[4]
    r = basic["c"].patch(f"/api/account/channels/{basic['monitor']}", json={"preset": "humor"}, headers=CSRF)
    assert r.status_code == 403
    assert creator["c"].patch(f"/api/account/channels/{creator['monitor']}", json={"preset": "nope"}, headers=CSRF).status_code == 422
    before = creator["c"].get("/api/account/subscription").json()["limits"]
    r = creator["c"].patch(f"/api/account/channels/{creator['monitor']}", json={"preset": "highlights"}, headers=CSRF)
    assert r.status_code == 200 and r.json()["preset"] == "highlights"
    assert creator["c"].get("/api/account/subscription").json()["limits"] == before
    presets = {p["id"]: p["allowed"] for p in basic["c"].get("/api/account/presets").json()}
    assert presets == {"balanced": True, "best": False, "humor": False, "highlights": False, "intense": False}
    # Following an already captured channel is still one channel of the plan
    usage = basic["c"].get("/api/account/subscription").json()["usage"]
    assert usage["channels"] == 1


def test_the_shared_capture_stops_only_when_nobody_needs_it(app, client, channel_url):
    from clipper import pipeline
    from clipper.db import session_scope

    a_id, a = subscriber(app, client, "basic")
    b_id, b = subscriber(app, client, "creator")
    ma = a.post("/api/account/channels", json={"url": channel_url}, headers=CSRF).json()["id"]
    b.post("/api/account/channels", json={"url": channel_url}, headers=CSRF)
    sid = _streamer_id(channel_url)
    with session_scope() as db:
        sess_id = pipeline.report_status(db, sid, live=True)["session_id"]
        pipeline.register_segments(db, sess_id, "w1", "http://seg", [{"idx": 0, "start": 0, "duration": 60}])
    a.delete(f"/api/account/channels/{ma}", headers=CSRF)
    with session_scope() as db:
        assert pipeline.session_state(db, sess_id)["stop"] is False
    deliver(client, {**snapshot(b_id, plan="basic", seq=2, status="ended", until=0), "limits": LIMITS["basic"]})
    with session_scope() as db:
        assert pipeline.session_state(db, sess_id)["stop"] is True, "no consumer left"
        # Segments after that are not analysed
        pipeline.register_segments(db, sess_id, "w1", "http://seg", [{"idx": 1, "start": 60, "duration": 60}])
        from clipper.models import Segment

        assert db.scalar(select(Segment.analysis_state).where(Segment.session_id == sess_id, Segment.idx == 1)) == "skipped"


def test_hts_channels_keep_running_for_the_hts_and_customers_share_them(app, client):
    from clipper import entitlements, pipeline
    from clipper.db import session_scope
    from clipper.models import Streamer

    name = f"hts{uuid.uuid4().hex[:8]}"
    with session_scope() as db:
        s = Streamer(platform="twitch", channel=name, url=f"https://www.twitch.tv/{name}")  # created by staff
        db.add(s)
        db.flush()
        sid = s.id
    uid, c = subscriber(app, client, "basic")
    from clipper import platforms
    from clipper.api import routes_account

    routes_account.parse_stream_url = lambda raw: platforms.StreamRef(platform="twitch", channel=name,
                                                                       url=f"https://www.twitch.tv/{name}", display_name=name)
    try:
        assert c.post("/api/account/channels", json={"url": f"https://www.twitch.tv/{name}"}, headers=CSRF).status_code == 201
    finally:
        routes_account.parse_stream_url = platforms.parse_stream_url
    with session_scope() as db:
        assert db.get(Streamer, sid).hts_monitoring is True, "a customer following never changes the HTS flag"
        sess_id = pipeline.report_status(db, sid, live=True)["session_id"]
        pipeline.register_segments(db, sess_id, "w1", "http://seg", [{"idx": 0, "start": 0, "duration": 600}])
        assert entitlements.usage_summary(db, uid)["live_seconds"] == 600
    deliver(client, {**snapshot(uid, plan="basic", seq=2, status="ended", until=0), "limits": LIMITS["basic"]})
    with session_scope() as db:
        assert pipeline.session_state(db, sess_id)["stop"] is False, "the HTS still needs it"
        pipeline.register_segments(db, sess_id, "w1", "http://seg", [{"idx": 1, "start": 600, "duration": 600}])
        from clipper.models import UsageEntry

        charged = db.scalar(select(func.sum(UsageEntry.amount)).where(UsageEntry.user_id == uid))
        assert charged == 600, "not charged after its access ended"


def test_second_pass_runs_only_for_moments_a_pro_customer_follows(app, client, channel_url):
    from clipper import entitlements, pipeline
    from clipper.db import session_scope
    from clipper.models import Clip, Streamer

    b_id, b = subscriber(app, client, "basic")
    p_id, p = subscriber(app, client, "pro", hours=1)
    b.post("/api/account/channels", json={"url": channel_url}, headers=CSRF)
    p.post("/api/account/channels", json={"url": channel_url}, headers=CSRF)
    sid = _streamer_id(channel_url)
    with session_scope() as db:
        sess_id = pipeline.report_status(db, sid, live=True)["session_id"]
        pipeline.register_segments(db, sess_id, "w1", "http://seg", [{"idx": 0, "start": 0, "duration": 3600}])
        pipeline.register_segments(db, sess_id, "w1", "http://seg", [{"idx": 1, "start": 3600, "duration": 600}])
        streamer = db.get(Streamer, sid)
        early = Clip(session_id=sess_id, streamer_id=sid, start=100, end=130, peak=110, score=60)
        late = Clip(session_id=sess_id, streamer_id=sid, start=3700, end=3730, peak=3710, score=60)
        db.add_all([early, late])
        db.flush()
        assert entitlements.has_feature(db, streamer, "secondPass", early) is True  # the Pro followed this part
        assert entitlements.has_feature(db, streamer, "secondPass", late) is False  # only the Basic is left


def test_migration_turns_owned_channels_into_monitors(tmp_path):
    """0017 -> 0018 on a database with a customer channel from PR #23: it becomes a monitor, nothing is lost."""
    import sqlalchemy as sa

    from clipper.migrate import upgrade_db

    url = f"sqlite:///{(tmp_path / 'm.db').as_posix()}"
    upgrade_db(url, "0017")
    eng = sa.create_engine(url)
    with eng.begin() as c:
        c.execute(sa.text("INSERT INTO users (id, email, password_hash, role, active, session_version, created_at) "
                          "VALUES (7, 'c@example.com', '', 'customer', 1, 0, CURRENT_TIMESTAMP)"))
        c.execute(sa.text("INSERT INTO streamers (id, platform, channel, url, display_name, enabled, preferences, status, "
                          "stream_title, last_error, check_requested, created_at, owner_id) VALUES (3, 'twitch', 'x', "
                          "'https://www.twitch.tv/x', '', 1, '{}', 'unknown', '', '', 1, CURRENT_TIMESTAMP, 7)"))
        c.execute(sa.text("INSERT INTO streamers (id, platform, channel, url, display_name, enabled, preferences, status, "
                          "stream_title, last_error, check_requested, created_at) VALUES (4, 'twitch', 'hts', "
                          "'https://www.twitch.tv/hts', '', 1, '{}', 'unknown', '', '', 1, CURRENT_TIMESTAMP)"))
    upgrade_db(url)
    with eng.begin() as c:
        assert c.execute(sa.text("SELECT user_id, streamer_id, preset FROM channel_monitors")).fetchall() == [(7, 3, "balanced")]
        flags = dict(c.execute(sa.text("SELECT id, hts_monitoring FROM streamers")).fetchall())
        assert bool(flags[3]) is False and bool(flags[4]) is True
    upgrade_db(url)  # idempotent: no duplicate monitor
    with eng.begin() as c:
        assert c.execute(sa.text("SELECT COUNT(*) FROM channel_monitors")).scalar() == 1
    eng.dispose()


def test_a_short_is_delivered_only_when_its_whole_interval_was_followed(app, client, channel_url):
    """Temporal coverage: the entire clip must lie inside the customer's followed window (0.5 s tolerance).

    Timeline (segments of 600 s): A from 0 with a 1-hour franchise; B joins at 600; C, D and E from 0;
    at 1800 C cancels, D is suspended and E is refunded."""
    from clipper import pipeline, sharing
    from clipper.db import session_scope
    from clipper.models import Clip

    a = subscriber(app, client, "pro", hours=1)
    c_ = subscriber(app, client, "pro")
    d = subscriber(app, client, "pro")
    e = subscriber(app, client, "pro")
    b = subscriber(app, client, "pro")
    for _, cl in (a, c_, d, e):
        assert cl.post("/api/account/channels", json={"url": channel_url}, headers=CSRF).status_code == 201
    sid = _streamer_id(channel_url)
    with session_scope() as db:
        sess_id = pipeline.report_status(db, sid, live=True)["session_id"]

    def seg(idx):
        with session_scope() as db:
            pipeline.register_segments(db, sess_id, "w1", "http://seg", [{"idx": idx, "start": idx * 600.0, "duration": 600.0}])

    def clip(start, end):
        with session_scope() as db:
            row = Clip(session_id=sess_id, streamer_id=sid, start=start, end=end, peak=start + 5, score=80, status="ready",
                       video_path=f"clips/{uuid.uuid4().hex}/v1.mp4", file_size=1_000_000)
            db.add(row)
            db.flush()
            sharing.on_clip_status(db, row)
            return row.id

    def library(cl):
        return {x["clip_id"] for x in cl.get("/api/library").json()["items"]}

    seg(0)
    assert b[1].post("/api/account/channels", json={"url": channel_url}, headers=CSRF).status_code == 201
    seg(1)  # B attached from 600
    seg(2)
    inside = clip(700, 730)  # fully inside everyone's window, delivered BEFORE the cancellations
    assert all(inside in library(cl) for _, cl in (a, b, c_, d, e))

    now = __import__("test_billing_entitlements")._now()
    ended = {"status": "ended", "until": now - 1}
    deliver(client, {**snapshot(c_[0], plan="pro", seq=2, financial="canceled", **ended), "limits": PLANS["pro"]})
    deliver(client, {**snapshot(d[0], plan="pro", seq=2, status="suspended"), "limits": PLANS["pro"]})
    deliver(client, {**snapshot(e[0], plan="pro", seq=2, financial="refunded", **ended), "limits": PLANS["pro"]})
    for idx in range(3, 7):
        seg(idx)  # A's franchise (3600 s) ends with segment 5

    before_join = clip(590, 620)  # starts before B joined
    after_quota = clip(3590, 3610)  # ends after A's franchise ran out
    crosses = clip(1790, 1810)  # crosses the cancellation / suspension / refund at 1800
    late = clip(1000, 1030)  # captured while access was valid, rendered after it ended
    rounding = clip(599.7, 630)  # 0.3 s before B joined: within the rounding tolerance

    lib = {name: library(cl) for name, (_, cl) in {"A": a, "B": b, "C": c_, "D": d, "E": e}.items()}
    assert before_join not in lib["B"] and before_join in lib["A"]
    assert rounding in lib["B"]
    assert after_quota not in lib["A"] and after_quota in lib["B"]
    for who in ("C", "D", "E"):
        assert crosses not in lib[who], who
    assert crosses in lib["A"] and crosses in lib["B"]
    # Followed and paid while valid -> delivered after the end (cancel, suspension); never after a refund
    assert late in lib["C"] and late in lib["D"] and late not in lib["E"]
    # Shorts legitimately delivered before stay available, also after cancellation or refund
    for who in ("C", "D", "E"):
        assert inside in lib[who], who
    assert e[1].get(f"/api/library/{inside}").status_code == 200


def _seed_0017(url: str, *, duplicate_lives: bool) -> None:
    import sqlalchemy as sa

    from clipper.migrate import upgrade_db

    upgrade_db(url, "0017")
    eng = sa.create_engine(url)
    with eng.begin() as c:
        c.execute(sa.text("INSERT INTO streamers (id, platform, channel, url, display_name, enabled, preferences, status, "
                          "stream_title, last_error, check_requested, created_at) VALUES (5, 'twitch', 'dup', "
                          "'https://www.twitch.tv/dup', '', 1, '{}', 'live', '', '', 0, CURRENT_TIMESTAMP)"))
        for sid in ((11, 12) if duplicate_lives else (11,)):
            c.execute(sa.text("INSERT INTO stream_sessions (id, streamer_id, status, title, started_at, end_reason, "
                              "capture_worker_id, segment_base_url, covered_until, analyzed_until, scored_until, "
                              "segment_count, stop_requested, stats) VALUES (:i, 5, 'live', '', CURRENT_TIMESTAMP, '', '', "
                              "'', 0, 0, 0, 0, 0, '{}')"), {"i": sid})
    eng.dispose()


def test_migration_0018_refuses_duplicate_lives_without_touching_data(tmp_path):
    import sqlalchemy as sa

    from clipper.migrate import upgrade_db

    url = f"sqlite:///{(tmp_path / 'dup.db').as_posix()}"
    _seed_0017(url, duplicate_lives=True)
    with pytest.raises(Exception) as err:
        upgrade_db(url)
    message = str(err.value)
    assert "uq_stream_sessions_one_live" in message and "streamer 5: 2 sessões live (ids 11..12)" in message
    eng = sa.create_engine(url)
    with eng.begin() as c:
        assert c.execute(sa.text("SELECT version_num FROM alembic_version")).scalar() == "0017"
        assert "channel_monitors" not in sa.inspect(c).get_table_names(), "nothing changed"
        assert c.execute(sa.text("SELECT COUNT(*) FROM stream_sessions WHERE status = 'live'")).scalar() == 2, "no data fixed"
        # The operator ends the duplicate, then the migration completes with the unique index
        c.execute(sa.text("UPDATE stream_sessions SET status = 'ended' WHERE id = 11"))
    eng.dispose()
    upgrade_db(url)
    eng = sa.create_engine(url)
    with eng.begin() as c:
        index = {i["name"]: i for i in sa.inspect(c).get_indexes("stream_sessions")}["uq_stream_sessions_one_live"]
        assert index["unique"]
        with pytest.raises(sa.exc.IntegrityError):
            c.execute(sa.text("INSERT INTO stream_sessions (id, streamer_id, status, title, started_at, end_reason, "
                              "capture_worker_id, segment_base_url, covered_until, analyzed_until, scored_until, "
                              "segment_count, stop_requested, stats) VALUES (13, 5, 'live', '', CURRENT_TIMESTAMP, '', '', "
                              "'', 0, 0, 0, 0, 0, '{}')"))
    eng.dispose()


def test_migration_0018_downgrade_and_repeat(tmp_path):
    import sqlalchemy as sa
    from alembic import command

    from clipper.migrate import alembic_config, upgrade_db

    url = f"sqlite:///{(tmp_path / 'rt.db').as_posix()}"
    _seed_0017(url, duplicate_lives=False)
    upgrade_db(url)
    upgrade_db(url)  # repeat: no-op
    command.downgrade(alembic_config(url), "0017")
    eng = sa.create_engine(url)
    with eng.begin() as c:
        insp = sa.inspect(c)
        assert c.execute(sa.text("SELECT version_num FROM alembic_version")).scalar() == "0017"
        assert {"channel_monitors", "monitor_sessions"}.isdisjoint(insp.get_table_names())
        assert "uq_stream_sessions_one_live" not in {i["name"] for i in insp.get_indexes("stream_sessions")}
        assert "hts_monitoring" not in {col["name"] for col in insp.get_columns("streamers")}
        assert c.execute(sa.text("SELECT COUNT(*) FROM stream_sessions")).scalar() == 1, "history kept"
    eng.dispose()
    upgrade_db(url)
    eng = sa.create_engine(url)
    with eng.begin() as c:
        assert c.execute(sa.text("SELECT version_num FROM alembic_version")).scalar() == "0018"
        assert "uq_stream_sessions_one_live" in {i["name"] for i in sa.inspect(c).get_indexes("stream_sessions")}
    eng.dispose()
