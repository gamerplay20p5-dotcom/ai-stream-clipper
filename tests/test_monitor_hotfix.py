"""v0.6.2-hotfix.1: the live monitor keeps making progress whatever a single channel, the network or the API does.

Real child processes are used where the property depends on them (a stuck probe is killed, nothing is left behind).
"""

from __future__ import annotations

import asyncio
import sys
import threading
import time
from datetime import datetime, timedelta

import psutil
import pytest

from clipper.workers.client import InternalError
from clipper.workers.monitor import Monitor, run_supervised
from clipper.workers.probe import ERR_TIMEOUT, ProbeResult, probe_isolated
from clipper.workers.runtime import LoopWatchdog
from conftest import CSRF
from test_api import _signed

HANG = [sys.executable, "-c", "import time; time.sleep(3600)"]
PY = sys.executable


def _children() -> list[psutil.Process]:
    return [c for c in psutil.Process().children(recursive=True) if c.is_running() and c.status() != psutil.STATUS_ZOMBIE]


class FakeAPI:
    """Internal API double: a channel stays due until a report for it is accepted."""

    def __init__(self, targets: list[dict], fail_polls: int = 0, fail_reports: int = 0):
        self.due = {t["streamer_id"]: t for t in targets}
        self.reports: list[dict] = []
        self.fail_polls = fail_polls
        self.fail_reports = fail_reports
        self.polls = 0

    async def get(self, path, **params):
        self.polls += 1
        if self.fail_polls > 0:
            self.fail_polls -= 1
            raise InternalError(0, "connection refused")
        return list(self.due.values())

    async def post(self, path, body=None, **params):
        if self.fail_reports > 0:
            self.fail_reports -= 1
            raise InternalError(503, "unavailable")
        self.reports.append(body)
        self.due.pop(body["streamer_id"], None)
        return {"created": False}


def _targets(n: int, platform: str = "twitch") -> list[dict]:
    return [{"streamer_id": i, "platform": platform, "url": f"https://example.invalid/{platform}/{i}"} for i in range(1, n + 1)]


async def _run_for(mon: Monitor, seconds: float) -> None:
    task = asyncio.create_task(mon.run())
    await asyncio.sleep(seconds)
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)
    await mon.cancel_in_flight()


# ---------------------------------------------------------------- probe isolation
def test_probe_blocked_forever_is_killed_at_the_deadline():
    before = {c.pid for c in _children()}
    t0 = time.monotonic()
    res = asyncio.run(probe_isolated("https://x.invalid", deadline_s=1.0, command=HANG))
    assert res.timed_out and res.error == ERR_TIMEOUT and not res.live
    assert time.monotonic() - t0 < 6
    assert {c.pid for c in _children()} <= before  # the stuck process does not outlive its deadline


def test_probe_result_is_parsed_from_the_child():
    cmd = [PY, "-c", 'print(\'{"live": true, "title": "Ao vivo", "error": ""}\')']
    res = asyncio.run(probe_isolated("u", 10, command=cmd))
    assert res.live and res.title == "Ao vivo" and not res.timed_out


def test_child_crash_is_an_error_not_a_hang():
    res = asyncio.run(probe_isolated("u", 10, command=[PY, "-c", "import sys; sys.exit(3)"]))
    assert not res.live and "sem resposta" in res.error


def test_dns_failure_inside_the_probe_is_reported(monkeypatch, capsys):
    import socket

    from clipper import platforms
    from clipper.workers import probe

    def boom(url, timeout=20.0):
        raise socket.gaierror(-3, "Temporary failure in name resolution")

    monkeypatch.setattr(platforms, "probe_live", boom)
    assert probe.main(["https://www.twitch.tv/x", "5"]) == 0
    out = capsys.readouterr().out
    assert '"live": false' in out and "name resolution" in out


def test_repeated_timeouts_leave_no_processes_or_threads():
    threads = threading.active_count()

    async def many():
        for _ in range(8):
            r = await probe_isolated("u", deadline_s=0.4, command=HANG)
            assert r.timed_out

    asyncio.run(many())
    assert not _children()
    assert threading.active_count() <= threads + 1


# ---------------------------------------------------------------- independence
def test_five_channels_one_stuck_the_other_four_are_reported_at_once():
    api = FakeAPI(_targets(5))

    async def probe(url, deadline, http):
        if url.endswith("/3"):
            return await probe_isolated(url, deadline_s=2.0, command=HANG)
        return ProbeResult(False)

    async def scenario():
        mon = Monitor(api, probe=probe, poll_every_s=0.05, concurrency=5)
        task = asyncio.create_task(mon.run())
        await asyncio.sleep(0.6)
        early = sorted(r["streamer_id"] for r in api.reports)
        await asyncio.sleep(2.8)
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        await mon.cancel_in_flight()
        return early, mon

    early, mon = asyncio.run(scenario())
    assert early == [1, 2, 4, 5]  # nobody waited for channel 3
    stuck = [r for r in api.reports if r["streamer_id"] == 3]
    assert stuck and stuck[0]["error"] == ERR_TIMEOUT and mon.counters["timeouts"] == 1
    assert not _children()


@pytest.mark.parametrize("platform", ["twitch", "youtube"])
def test_partial_platform_failure_is_isolated_per_channel(platform):
    api = FakeAPI(_targets(4, platform) + [{"streamer_id": 9, "platform": "twitch" if platform == "youtube" else "youtube",
                                            "url": "https://other/9"}])

    async def probe(url, deadline, http):
        if platform in url and url.endswith(("/1", "/2")):
            return ProbeResult(False, error=f"{platform}: 503 from the platform")
        return ProbeResult(url.endswith("/4"), title="live" if url.endswith("/4") else "")

    mon = Monitor(api, probe=probe, poll_every_s=0.05)
    asyncio.run(_run_for(mon, 0.5))
    by = {r["streamer_id"]: r for r in api.reports}
    assert set(by) == {1, 2, 3, 4, 9}
    assert by[1]["error"] and by[2]["error"] and not by[3]["error"] and by[4]["live"] and not by[9]["error"]
    assert mon.counters["probes_failed"] == 2


def test_one_probe_per_channel_in_flight():
    calls: list[str] = []

    async def probe(url, deadline, http):
        calls.append(url)
        await asyncio.sleep(1.0)
        return ProbeResult(False)

    api = FakeAPI(_targets(2))
    mon = Monitor(api, probe=probe, poll_every_s=0.05)
    asyncio.run(_run_for(mon, 0.5))  # ~10 polls while the probes are still running
    assert sorted(calls) == sorted(t["url"] for t in _targets(2))


# ---------------------------------------------------------------- API unavailable
def test_internal_api_unavailable_then_back():
    api = FakeAPI(_targets(3), fail_polls=3, fail_reports=1)

    async def probe(url, deadline, http):
        return ProbeResult(False)

    mon = Monitor(api, probe=probe, poll_every_s=0.05)
    asyncio.run(_run_for(mon, 0.8))
    assert mon.counters["poll_failures"] == 3 and mon.counters["report_failures"] == 1
    assert sorted(r["streamer_id"] for r in api.reports) == [1, 2, 3]  # the failed report was retried
    assert mon.snapshot()["monitor"]["last_report_ok_at"]


# ---------------------------------------------------------------- supervision / no progress
class StuckAPI(FakeAPI):
    async def get(self, path, **params):  # the poll itself never returns
        await asyncio.sleep(3600)


def test_supervisor_restarts_a_monitor_without_progress_and_keeps_captures():
    async def scenario():
        mon = Monitor(StuckAPI([]), stall_s=0.3, poll_every_s=0.05)
        capture = asyncio.create_task(asyncio.sleep(3600))  # an active capture job in the same worker
        sup = asyncio.create_task(run_supervised(mon, check_every_s=0.1))
        await asyncio.sleep(1.6)
        snap = mon.snapshot()["monitor"]
        alive = not capture.done()
        sup.cancel()
        await asyncio.gather(sup, return_exceptions=True)
        capture.cancel()
        return mon, snap, alive

    mon, snap, capture_alive = asyncio.run(scenario())
    assert mon.counters["restarts"] >= 1
    assert "stalled" in snap and "restarts" in snap  # progress is part of the heartbeat
    assert capture_alive  # restarting the monitor never touches captures


def test_supervisor_restarts_a_crashed_monitor():
    class Boom(FakeAPI):
        async def get(self, path, **params):
            raise RuntimeError("bug")

    async def scenario():
        mon = Monitor(Boom([]), poll_every_s=0.05)
        sup = asyncio.create_task(run_supervised(mon, check_every_s=0.05))
        await asyncio.sleep(0.4)
        sup.cancel()
        await asyncio.gather(sup, return_exceptions=True)
        return mon

    mon = asyncio.run(scenario())
    assert mon.counters["poll_failures"] >= 2  # errors are caught inside: the loop keeps polling


def test_blocked_event_loop_triggers_a_controlled_exit():
    exits: list[int] = []
    dog = LoopWatchdog(stall_s=0.2, exit_fn=exits.append)
    dog.tick()
    assert dog.check() is False and not exits
    dog.last_tick -= 1.0  # the loop did not tick for 1 s (blocked)
    assert dog.check() is True and exits == [70]
    assert dog.check() is False and exits == [70]  # fires once
    assert LoopWatchdog(stall_s=0, exit_fn=exits.append).check() is False  # disabled


# ---------------------------------------------------------------- panel state
def _streamer(**kw):
    from clipper.models import Streamer

    base = dict(platform="twitch", channel="x", url="u", enabled=True, status="offline", last_error="", check_requested=False,
                last_checked_at=datetime(2026, 11, 19, 10, 0, 0))
    base.update(kw)
    return Streamer(**base)


def test_stale_status_is_flagged_even_if_the_last_result_was_live():
    from clipper.api.serializers import monitor_state

    now = datetime(2026, 11, 19, 19, 48, 0)  # 9.8 h later
    st = monitor_state(_streamer(status="live"), now=now, stale_after_s=300)
    assert st["state"] == "stale" and st["last_checked_age_s"] == 35280
    assert monitor_state(_streamer(status="live"), now=datetime(2026, 11, 19, 10, 1), stale_after_s=300)["state"] == "live"
    assert monitor_state(_streamer(enabled=False), now=now, stale_after_s=300)["state"] == "paused"
    assert monitor_state(_streamer(last_error="503", status="error"), now=datetime(2026, 11, 19, 10, 1),
                         stale_after_s=300)["state"] == "error"


def test_manual_request_not_processed_is_visible():
    from clipper.api.serializers import monitor_state

    recent = datetime(2026, 11, 19, 10, 0, 30)
    assert monitor_state(_streamer(check_requested=True), now=recent, stale_after_s=300) == {
        "state": "checking", "check_requested": True, "last_checked_age_s": 30, "stale_after_s": 300}
    late = monitor_state(_streamer(check_requested=True), now=recent + timedelta(hours=9), stale_after_s=300)
    assert late["state"] == "stale" and late["check_requested"] is True  # requested, but nobody is executing it


def test_api_exposes_monitor_state_and_check_flag(admin, client):
    r = admin.post("/api/streamers", json={"url": "file://gameplay_event.mp4"}, headers=CSRF)
    sid = r.json()["id"]
    try:
        assert admin.post(f"/api/streamers/{sid}/check", headers=CSRF).status_code == 200
        s = next(x for x in admin.get("/api/streamers").json() if x["id"] == sid)
        assert s["monitor"]["check_requested"] is True and s["monitor"]["state"] in ("checking", "stale")
        _signed(client, "POST", "/internal/monitor/report", {"streamer_id": sid, "live": False})
        s = next(x for x in admin.get("/api/streamers").json() if x["id"] == sid)
        assert s["monitor"]["check_requested"] is False and s["monitor"]["state"] == "offline"
    finally:
        admin.delete(f"/api/streamers/{sid}", headers=CSRF)


def test_recovery_reports_do_not_duplicate_sessions(admin, client):
    """After a restart the monitor reports a live channel again: same session, one capture job."""
    r = admin.post("/api/streamers", json={"url": "file://gameplay_event.mp4"}, headers=CSRF)
    sid = r.json()["id"]
    try:
        first = _signed(client, "POST", "/internal/monitor/report", {"streamer_id": sid, "live": True}).json()
        for _ in range(3):  # supervisor restarts / watchdog restart re-report the same live channel
            again = _signed(client, "POST", "/internal/monitor/report", {"streamer_id": sid, "live": True}).json()
            assert again["created"] is False and again["session_id"] == first["session_id"]
        jobs = admin.get("/api/jobs?type=capture").json()
        assert sum(1 for j in jobs if j["payload"].get("session_id") == first["session_id"]) == 1
    finally:
        admin.delete(f"/api/streamers/{sid}", headers=CSRF)


def test_settings_defaults_are_bounded():
    from clipper.config import Settings

    s = Settings()
    assert 0 < s.monitor_probe_timeout_s < s.monitor_stall_s and s.monitor_concurrency >= 1
    assert s.loop_stall_exit_s > s.monitor_probe_timeout_s  # a slow probe never looks like a blocked loop
