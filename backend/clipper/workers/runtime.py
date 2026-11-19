"""Worker runtime: registration, heartbeat, job claiming, lease renewal, graceful shutdown."""

from __future__ import annotations

import asyncio
import faulthandler
import logging
import os
import signal
import socket
import sys
import tempfile
import threading
import time
import traceback
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path

from .. import __version__
from ..config import Settings
from . import sysinfo
from .client import InternalClient, InternalError

log = logging.getLogger(__name__)
HEARTBEAT_FILE = Path(tempfile.gettempdir()) / "asc-worker-heartbeat"


class LoopWatchdog:
    """A plain thread that notices when the asyncio event loop stops running.

    The heartbeat is a task of that same loop, so a blocked loop silences the heartbeat, the job claims, the lease
    renewals and the monitor at once: the worker looks "lost" but its container stays up and is never restarted.
    After `stall_s` without a tick the watchdog dumps every thread's stack to stderr (the evidence for the logs)
    and exits the process with code 70, so the container restart policy brings a clean worker back. In-flight jobs
    are recovered by lease expiry, exactly like a crash. `stall_s <= 0` disables it.
    """

    def __init__(self, stall_s: float, exit_fn: Callable[[int], None] | None = None, check_every_s: float = 5.0):
        self.stall_s = stall_s
        self.exit_fn = exit_fn or os._exit
        self.check_every_s = check_every_s
        self.last_tick = time.monotonic()
        self.fired = False
        self._halt = threading.Event()
        self._thread: threading.Thread | None = None

    def tick(self) -> None:
        self.last_tick = time.monotonic()

    def check(self) -> bool:
        """True when the loop is considered stuck (and the exit was triggered)."""
        if self.stall_s <= 0 or self.fired:
            return False
        blocked = time.monotonic() - self.last_tick
        if blocked < self.stall_s:
            return False
        self.fired = True
        sys.stderr.write(f"asc-worker: event loop blocked for {blocked:.0f}s; dumping stacks and exiting (70)\n")
        try:
            faulthandler.dump_traceback(file=sys.stderr, all_threads=True)
        except Exception:  # pragma: no cover
            pass
        log.critical("event loop blocked, exiting for a controlled restart", extra={"blocked_s": round(blocked)})
        self.exit_fn(70)
        return True

    def start(self) -> None:
        if self.stall_s <= 0:
            return

        def _run() -> None:
            while not self._halt.wait(self.check_every_s):
                if self.check():
                    return

        self._thread = threading.Thread(target=_run, name="asc-loop-watchdog", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._halt.set()


def enable_stack_dumps() -> None:
    """`kill -USR1 <pid>` (or `docker kill -s USR1`) prints every thread's stack without stopping the worker."""
    try:
        faulthandler.enable(file=sys.stderr)
        if hasattr(signal, "SIGUSR1"):
            faulthandler.register(signal.SIGUSR1, file=sys.stderr, all_threads=True)
    except (RuntimeError, ValueError, AttributeError, OSError):  # pragma: no cover - e.g. no real stderr
        pass


class JobError(Exception):
    def __init__(self, message: str, retryable: bool = True):
        super().__init__(message)
        self.retryable = retryable


class JobCancelled(Exception):
    pass


@dataclass
class JobContext:
    id: int
    type: str
    payload: dict
    attempts: int
    client: InternalClient
    settings: Settings
    workdir: Path
    cancel: asyncio.Event = field(default_factory=asyncio.Event)

    def check_cancel(self) -> None:
        if self.cancel.is_set():
            raise JobCancelled()


Handler = Callable[[JobContext], Awaitable[dict | None]]


@dataclass
class RoleSpec:
    job_type: str
    handler: Handler
    concurrency: int


class WorkerRuntime:
    def __init__(self, settings: Settings, roles: list[str], specs: list[RoleSpec],
                 background: list[Callable[[WorkerRuntime], Awaitable[None]]] | None = None,
                 base_url: str = "", extra_caps: dict | None = None,
                 metrics_hook: Callable[[], dict] | None = None):
        settings.require_secrets("internal_secret")
        self.settings = settings
        self.worker_id = settings.resolved_worker_id()
        self.roles = roles
        self.specs = specs
        self.background = background or []
        self.base_url = base_url
        self.extra_caps = extra_caps or {}
        self.metrics_hook = metrics_hook
        self.extra_metrics: list[Callable[[], dict]] = []  # e.g. the monitor's progress snapshot
        self.watchdog = LoopWatchdog(getattr(settings, "loop_stall_exit_s", 0.0))
        self.client = InternalClient(settings.api_url, settings.internal_secret, self.worker_id)
        self.running: dict[int, JobContext] = {}
        self._stop = asyncio.Event()
        self._job_tasks: set[asyncio.Task] = set()
        self.workdir = Path(settings.work_dir) / self.worker_id
        self.workdir.mkdir(parents=True, exist_ok=True)

    # -- lifecycle ----------------------------------------------------------------
    async def register(self) -> None:
        caps = sysinfo.capabilities(self.settings.gpu_device, self.extra_caps)
        delay = 2.0
        while not self._stop.is_set():
            try:
                await self.client.post("/internal/workers/register", {
                    "host": socket.gethostname(), "host_label": self.settings.worker_host_label,
                    "roles": self.roles, "capabilities": caps, "base_url": self.base_url, "version": __version__,
                })
                log.info("registered", extra={"worker": self.worker_id, "roles": self.roles, "gpu": caps.get("gpu")})
                return
            except InternalError as exc:
                log.warning("registration failed, retrying", extra={"err": str(exc)})
                await asyncio.sleep(delay)
                delay = min(60.0, delay * 2)

    async def _heartbeat_loop(self) -> None:
        sysinfo.psutil.cpu_percent(interval=None)
        while not self._stop.is_set():
            try:
                m = sysinfo.metrics(self.workdir, len(self.running))
                for hook in ([self.metrics_hook] if self.metrics_hook else []) + self.extra_metrics:
                    try:
                        m.update(hook())
                    except Exception as exc:  # metrics must never break the heartbeat
                        log.warning("metrics hook failed", extra={"err": str(exc)[:200]})
                await self.client.post("/internal/workers/heartbeat", {"metrics": m})
                HEARTBEAT_FILE.write_text(str(time.time()))  # read by `clipper healthcheck`
            except InternalError as exc:
                if exc.status == 409:
                    await self.register()
                else:
                    log.warning("heartbeat failed", extra={"err": str(exc)})
            await self._sleep(self.settings.heartbeat_interval_s)

    async def _sleep(self, s: float) -> None:
        try:
            await asyncio.wait_for(self._stop.wait(), timeout=s)
        except TimeoutError:
            pass

    async def _claim_loop(self, spec: RoleSpec) -> None:
        sem = asyncio.Semaphore(spec.concurrency)
        while not self._stop.is_set():
            await sem.acquire()
            if self._stop.is_set():
                sem.release()
                break
            try:
                jobs = await self.client.post("/internal/jobs/claim", {"types": [spec.job_type], "limit": 1})
            except InternalError as exc:
                sem.release()
                if exc.status == 409:
                    await self.register()
                else:
                    log.warning("claim failed", extra={"err": str(exc)})
                await self._sleep(self.settings.poll_interval_s * 2)
                continue
            if not jobs:
                sem.release()
                await self._sleep(self.settings.poll_interval_s)
                continue
            j = jobs[0]
            ctx = JobContext(j["id"], j["type"], j["payload"], j["attempts"], self.client, self.settings,
                             self.workdir / f"job_{j['id']}")
            t = asyncio.create_task(self._run_job(spec, ctx, sem))
            self._job_tasks.add(t)
            t.add_done_callback(self._job_tasks.discard)

    async def _lease_loop(self, ctx: JobContext) -> None:
        interval = max(5.0, self.settings.lease_seconds / 3)
        while True:
            await asyncio.sleep(interval)
            try:
                r = await self.client.post(f"/internal/jobs/{ctx.id}/heartbeat")
                if not r.get("ok") or r.get("cancel"):
                    log.info("job cancellation requested", extra={"job_id": ctx.id})
                    ctx.cancel.set()
                    return
            except InternalError as exc:
                log.warning("lease renewal failed", extra={"job_id": ctx.id, "err": str(exc)})

    async def _run_job(self, spec: RoleSpec, ctx: JobContext, sem: asyncio.Semaphore) -> None:
        self.running[ctx.id] = ctx
        ctx.workdir.mkdir(parents=True, exist_ok=True)
        lease = asyncio.create_task(self._lease_loop(ctx))
        t0 = time.monotonic()
        log.info("job started", extra={"job_id": ctx.id, "job_type": ctx.type, "attempt": ctx.attempts})
        try:
            result = await spec.handler(ctx)
            await self.client.post(f"/internal/jobs/{ctx.id}/complete", {"result": result or {}})
            log.info("job finished", extra={"job_id": ctx.id, "job_type": ctx.type,
                                            "seconds": round(time.monotonic() - t0, 2)})
        except JobCancelled:
            await self._safe_fail(ctx, "cancelled", retryable=False)
        except asyncio.CancelledError:
            await self._safe_fail(ctx, "worker shutting down", retryable=True)
            raise
        except JobError as exc:
            log.warning("job failed", extra={"job_id": ctx.id, "job_type": ctx.type, "err": str(exc),
                                             "retryable": exc.retryable})
            await self._safe_fail(ctx, str(exc), exc.retryable)
        except Exception as exc:
            log.exception("job crashed", extra={"job_id": ctx.id, "job_type": ctx.type})
            await self._safe_fail(ctx, f"{type(exc).__name__}: {exc}\n{traceback.format_exc()[-2000:]}", True)
        finally:
            lease.cancel()
            self.running.pop(ctx.id, None)
            sem.release()
            _rmtree(ctx.workdir)

    async def _safe_fail(self, ctx: JobContext, error: str, retryable: bool) -> None:
        try:
            await self.client.post(f"/internal/jobs/{ctx.id}/fail", {"error": error[:7900], "retryable": retryable})
        except InternalError:
            log.warning("could not report failure; lease expiry will recover", extra={"job_id": ctx.id})

    async def _tick_loop(self) -> None:
        while True:
            self.watchdog.tick()
            await asyncio.sleep(2.0)

    async def run(self) -> None:
        enable_stack_dumps()
        self.watchdog.start()
        await self.register()
        bg = [asyncio.create_task(self._tick_loop()), asyncio.create_task(self._heartbeat_loop())]
        bg += [asyncio.create_task(fn(self)) for fn in self.background]
        loops = [asyncio.create_task(self._claim_loop(s)) for s in self.specs]
        try:
            await self._stop.wait()
        finally:
            for t in loops:
                t.cancel()
            await asyncio.gather(*loops, return_exceptions=True)
            jobs = list(self._job_tasks)
            for t in jobs:  # in-flight jobs report "worker shutting down" -> re-queued at once
                t.cancel()
            await asyncio.gather(*jobs, return_exceptions=True)
            for t in bg:
                t.cancel()
            await asyncio.gather(*bg, return_exceptions=True)
            self.watchdog.stop()
            await self.client.aclose()

    def stop(self) -> None:
        self._stop.set()


def _rmtree(p: Path) -> None:
    import shutil

    shutil.rmtree(p, ignore_errors=True)
