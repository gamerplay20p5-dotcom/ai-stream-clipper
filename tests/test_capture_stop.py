"""v0.6.2-hotfix.2: stopping a capture must never spin the event loop.

Incident 2026-11-19 (proven on 206): SPEED's session reached the 12 h limit at 10:23:54 UTC; `_watch` set
reason="max_duration", called `terminate()` (no await inside) and `continue`d without yielding. The event loop never
ran again, so ffmpeg's exit was never registered: the main thread spun at 100% CPU for 15 h, freezing the heartbeat,
the live monitor, the segment server and every other capture of the worker.
"""

from __future__ import annotations

import asyncio
import sys
import time
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from clipper.config import Settings
from clipper.workers.capture import CaptureManager
from clipper.workers.segment_store import SegmentStore

SLEEPER = [sys.executable, "-c", "import time; time.sleep(3600)"]


class FakeClient:
    def __init__(self, state: dict | None = None):
        self.state = state or {}
        self.posts: list[tuple[str, dict]] = []

    async def get(self, path, **params):
        return self.state

    async def post(self, path, body=None, **params):
        self.posts.append((path, body or {}))
        return {}


def _manager(tmp_path) -> CaptureManager:
    s = Settings(work_dir=tmp_path / "work", max_session_hours=12)
    return CaptureManager(s, SegmentStore(tmp_path / "capture"), "http://127.0.0.1:1")


def _ctx(client, payload=None):
    ev = asyncio.Event()
    return SimpleNamespace(client=client, cancel=ev, attempts=1, payload=payload or {},
                           check_cancel=lambda: None)


def test_reaching_the_deadline_with_live_processes_does_not_freeze_the_loop(tmp_path):
    """The exact incident path: deadline passed while ffmpeg/streamlink are running."""
    mgr = _manager(tmp_path)

    async def scenario():
        ticks = 0

        async def ticker():  # stands for the heartbeat / monitor sharing the same loop
            nonlocal ticks
            while True:
                ticks += 1
                await asyncio.sleep(0.05)

        t = asyncio.create_task(ticker())
        sdir = mgr.store.session_dir(1)
        procs = await mgr._start(list(SLEEPER), list(SLEEPER), sdir)  # "streamlink" | "ffmpeg"
        started = time.monotonic()
        result = await asyncio.wait_for(
            mgr._watch(_ctx(FakeClient()), 1, sdir, procs, 1001, 0.0, 0, 0.0, deadline=time.time() - 1), timeout=30)
        elapsed = time.monotonic() - started
        t.cancel()
        return result, elapsed, ticks, procs

    (produced, next_idx, covered, reason), elapsed, ticks, procs = asyncio.run(scenario())
    assert reason == "max_duration"
    assert elapsed < 20
    assert ticks >= 1  # the loop kept running while the capture stopped (the old code never returned at all)
    assert procs.ffmpeg.returncode is not None and procs.streamlink.returncode is not None


def test_cancel_and_stop_take_the_same_safe_path(tmp_path):
    mgr = _manager(tmp_path)

    async def scenario():
        sdir = mgr.store.session_dir(2)
        procs = await mgr._start([], list(SLEEPER), sdir)
        ctx = _ctx(FakeClient())
        ctx.cancel.set()
        return await asyncio.wait_for(mgr._watch(ctx, 2, sdir, procs, 1001, 0.0, 0, 0.0, deadline=time.time() + 3600),
                                      timeout=30)

    assert asyncio.run(scenario())[3] == "cancelled"


def test_resumed_capture_past_the_deadline_ends_without_starting_processes(tmp_path):
    """Sessions 19 and 20 were >12 h old when the worker froze: resuming them must not start (and spin) again."""
    mgr = _manager(tmp_path)
    started = (datetime.now(UTC) - timedelta(hours=13)).replace(tzinfo=None).isoformat() + "Z"
    client = FakeClient({"status": "live", "stop": False, "started_at": started, "next_idx": 10, "covered_until": 100.0})

    async def boom(*a, **k):
        raise AssertionError("must not start streamlink/ffmpeg after the deadline")

    mgr._start = boom
    ctx = _ctx(client, {"session_id": 20, "platform": "twitch", "url": "https://www.twitch.tv/x"})
    ctx.client = client
    result = asyncio.run(mgr.handle(ctx))
    assert result["end_reason"] == "max_duration"
    assert ("/internal/sessions/20/end", {"reason": "max_duration"}) in client.posts


@pytest.mark.skipif(sys.platform == "win32", reason="SIGTERM semantics are POSIX (production runs on Linux)")
def test_a_process_ignoring_sigterm_is_killed(tmp_path):
    mgr = _manager(tmp_path)
    stubborn = [sys.executable, "-c", "import signal, time; signal.signal(signal.SIGTERM, signal.SIG_IGN); time.sleep(3600)"]

    async def scenario():
        sdir = mgr.store.session_dir(3)
        procs = await mgr._start([], stubborn, sdir)
        await asyncio.sleep(0.5)  # let the child install its handler
        r = await asyncio.wait_for(mgr._watch(_ctx(FakeClient()), 3, sdir, procs, 1001, 0.0, 0, 0.0,
                                              deadline=time.time() - 1), timeout=40)
        return r, procs

    (_, _, _, reason), procs = asyncio.run(scenario())
    assert reason == "max_duration" and procs.ffmpeg.returncode is not None
