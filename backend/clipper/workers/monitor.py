"""Live monitoring: one independent probe per channel, hard deadlines, supervision and progress metrics.

v0.6.2 ran a whole round with ``asyncio.gather`` over ``asyncio.to_thread(probe_live)``: a single probe that never
returned held the round forever, and every other channel stopped being checked. Here:

* each channel is checked by its own task; the poll loop never waits for probes to finish;
* at most one probe per channel is in flight, and ``concurrency`` limits probes overall;
* every probe runs in a child process killed at ``deadline_s`` (see ``probe.py``), so nothing piles up;
* failures are recorded per channel and reported to the API like any other result (the panel shows the error);
* a supervisor restarts the poll loop if it stops making attempts, cancelling (and killing) in-flight probes;
* ``snapshot()`` goes into the worker heartbeat: last poll, last successful report, timeouts, restarts.

Captures are separate queued jobs in the same worker and are never touched by a monitor restart.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable

from .client import InternalError
from .probe import ProbeResult, probe_isolated

log = logging.getLogger(__name__)
Probe = Callable[[str, float, float], Awaitable[ProbeResult]]


def _iso(ts: float | None) -> str | None:
    if not ts:
        return None
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(ts))


class Monitor:
    def __init__(self, client, *, interval_s: float = 60.0, concurrency: int = 4, deadline_s: float = 45.0,
                 http_timeout_s: float = 20.0, stall_s: float = 600.0, poll_every_s: float = 5.0,
                 probe: Probe | None = None, clock: Callable[[], float] = time.time):
        self.client = client
        self.interval_s = interval_s
        self.deadline_s = deadline_s
        self.http_timeout_s = http_timeout_s
        self.stall_s = stall_s
        self.poll_every_s = poll_every_s
        self.probe: Probe = probe or (lambda url, deadline, http: probe_isolated(url, deadline, http))
        self.clock = clock
        self.sem = asyncio.Semaphore(max(1, concurrency))
        self.in_flight: dict[int, asyncio.Task] = {}
        self.started_at = clock()
        self.last_poll_attempt_at: float | None = None
        self.last_poll_ok_at: float | None = None
        self.last_report_ok_at: float | None = None
        self.counters = {"probes_ok": 0, "probes_failed": 0, "timeouts": 0, "report_failures": 0, "poll_failures": 0,
                         "restarts": 0}
        self.last_error = ""

    # -- one poll loop (supervised by run_supervised) -------------------------------------------
    async def run(self) -> None:
        while True:
            try:
                targets = await self.client.get("/internal/monitor/targets", min_interval=self.interval_s)
                self.last_poll_ok_at = self.clock()
                for t in targets:
                    sid = int(t["streamer_id"])
                    if sid in self.in_flight:  # one probe per channel at a time; a slow one never blocks others
                        continue
                    task = asyncio.create_task(self._check(t), name=f"probe-{sid}")
                    self.in_flight[sid] = task
                    task.add_done_callback(lambda _t, sid=sid: self.in_flight.pop(sid, None))
            except InternalError as exc:
                self.counters["poll_failures"] += 1
                self.last_error = f"API indisponível: {str(exc)[:200]}"
                log.warning("monitor poll failed", extra={"err": str(exc)[:200]})
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # never let one bad answer stop monitoring
                self.counters["poll_failures"] += 1
                self.last_error = f"{type(exc).__name__}: {str(exc)[:200]}"
                log.exception("monitor loop error")
            finally:
                self.last_poll_attempt_at = self.clock()
            await asyncio.sleep(self.poll_every_s)

    async def _check(self, t: dict) -> None:
        sid = int(t["streamer_id"])
        async with self.sem:
            if t.get("platform") == "file":
                res = ProbeResult(True)
            else:
                res = await self.probe(t["url"], self.deadline_s, self.http_timeout_s)
        if res.timed_out:
            self.counters["timeouts"] += 1
        if res.error:
            self.counters["probes_failed"] += 1
            log.warning("probe failed", extra={"streamer_id": sid, "platform": t.get("platform"), "err": res.error[:200],
                                               "elapsed_s": res.elapsed_s, "timed_out": res.timed_out})
        else:
            self.counters["probes_ok"] += 1
        try:
            r = await self.client.post("/internal/monitor/report", {
                "streamer_id": sid, "live": res.live, "title": res.title, "error": res.error, "note": res.note,
                "channel_id": res.channel_id})
            self.last_report_ok_at = self.clock()
            if r.get("created"):
                log.info("live detected, session created", extra={"streamer_id": sid, "session_id": r.get("session_id")})
        except InternalError as exc:  # the channel stays due: the next poll retries it
            self.counters["report_failures"] += 1
            log.warning("monitor report failed", extra={"streamer_id": sid, "err": str(exc)[:200]})

    # -- supervision ------------------------------------------------------------------------------
    def stalled(self) -> bool:
        """The poll loop made no attempt (ok or failed) for `stall_s`: it is stuck, not merely failing."""
        ref = self.last_poll_attempt_at or self.started_at
        return self.clock() - ref > self.stall_s

    async def cancel_in_flight(self) -> None:
        tasks = list(self.in_flight.values())
        for task in tasks:
            task.cancel()  # probe_isolated kills its child process on cancellation
        await asyncio.gather(*tasks, return_exceptions=True)
        self.in_flight.clear()

    def snapshot(self) -> dict:
        return {"monitor": {
            "last_poll_ok_at": _iso(self.last_poll_ok_at), "last_poll_attempt_at": _iso(self.last_poll_attempt_at),
            "last_report_ok_at": _iso(self.last_report_ok_at), "in_flight": len(self.in_flight),
            "stalled": self.stalled(), "last_error": self.last_error[:200], **self.counters,
        }}


async def run_supervised(mon: Monitor, check_every_s: float = 15.0) -> None:
    """Keep `mon.run()` alive: restart it if it crashes or stops making progress (with backoff)."""
    backoff = 1.0
    while True:
        mon.last_poll_attempt_at = mon.clock()  # a fresh start gets a full stall window
        began = mon.clock()
        task = asyncio.create_task(mon.run(), name="monitor-poll")
        try:
            while not task.done():
                await asyncio.wait({task}, timeout=check_every_s)
                if not task.done() and mon.stalled():
                    log.error("monitor stalled, restarting", extra={"stall_s": mon.stall_s, "in_flight": len(mon.in_flight)})
                    task.cancel()
                    break
        except asyncio.CancelledError:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            await mon.cancel_in_flight()
            raise
        result = (await asyncio.gather(task, return_exceptions=True))[0]
        if isinstance(result, BaseException) and not isinstance(result, asyncio.CancelledError):
            log.error("monitor loop crashed, restarting", extra={"err": f"{type(result).__name__}: {str(result)[:200]}"})
        await mon.cancel_in_flight()
        mon.counters["restarts"] += 1
        backoff = 1.0 if mon.clock() - began > 300 else min(60.0, backoff * 2)  # a healthy run resets the backoff
        await asyncio.sleep(backoff)
