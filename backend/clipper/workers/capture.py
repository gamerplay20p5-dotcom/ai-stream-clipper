"""Capture worker: live stream -> small indexed MPEG-TS segments (ring buffer).

* Twitch / YouTube: ``streamlink --stdout`` piped into ``ffmpeg -f segment -c copy`` (no
  re-encoding). Streamlink only receives canonical URLs built by ``platforms.py``.
* ``file`` sources (controlled test videos): ffmpeg reads the file (optionally at native
  speed with ``-re``) - used for reproducible end-to-end tests.

Segment timestamps come from ffmpeg's CSV segment list, so offsets are exact within a run;
reconnects start a new ``run_id`` placed at ``max(covered_until, wall-clock elapsed)``.
"""

from __future__ import annotations

import asyncio
import csv
import logging
import os
import sys
import time
from collections import deque
from datetime import UTC, datetime
from pathlib import Path

from .. import media
from ..config import Settings
from ..platforms import parse_file_source, probe_live
from ..security import sha256_file
from .client import InternalError
from .runtime import JobContext, JobError
from .segment_store import LocalSegment, SegmentStore

log = logging.getLogger(__name__)


class CaptureManager:
    def __init__(self, settings: Settings, store: SegmentStore, public_url: str):
        self.settings = settings
        self.store = store
        self.public_url = public_url.rstrip("/")

    # ------------------------------------------------------------------ commands
    def _streamlink_cmd(self, url: str) -> list[str]:
        # run with this interpreter: works inside venvs/containers without relying on PATH
        return [sys.executable, "-m", "streamlink", "--stdout", "--loglevel", "warning", "--twitch-disable-ads",
                "--hls-live-edge", "3", "--stream-segment-threads", "2", "--retry-open", "2",
                "--stream-timeout", "40", url, self.settings.capture_quality]

    def _ffmpeg_cmd(self, input_args: list[str], run_id: int, start_number: int) -> list[str]:
        return [
            media.FFMPEG, "-hide_banner", "-loglevel", "warning", "-nostdin",
            *input_args,
            "-map", "0:v:0", "-map", "0:a:0?", "-c", "copy",
            "-f", "segment", "-segment_time", str(self.settings.segment_seconds),
            "-segment_format", "mpegts", "-segment_list", f"run_{run_id}.csv", "-segment_list_type", "csv",
            "-segment_list_flags", "+live", "-segment_start_number", str(start_number),
            "seg_%06d.ts",
        ]

    # ------------------------------------------------------------------ handler
    async def handle(self, ctx: JobContext) -> dict:
        p = ctx.payload
        sid = int(p["session_id"])
        client = ctx.client
        state = await client.get(f"/internal/sessions/{sid}/state")
        if state["status"] != "live":
            return {"skipped": "session not live"}
        if state["stop"]:
            await client.post(f"/internal/sessions/{sid}/end", {"reason": "stopped before capture"})
            return {"stopped": True}
        sdir = self.store.session_dir(sid)
        started_at = datetime.fromisoformat(state["started_at"].rstrip("Z")).replace(tzinfo=UTC)
        deadline = started_at.timestamp() + self.settings.max_session_hours * 3600
        next_idx = int(state["next_idx"])
        covered = float(state["covered_until"])
        failures = 0
        run_id = ctx.attempts * 1000
        total_segments = 0
        end_reason = ""

        while True:
            ctx.check_cancel()
            run_id += 1
            elapsed = time.time() - started_at.timestamp()
            if p["platform"] == "file":
                run_base = covered
                ref = parse_file_source(p["url"], self.settings.test_media_dir)
                path = (self.settings.test_media_dir / ref.channel).resolve()
                inp = (["-re"] if self.settings.file_source_realtime else []) + \
                    (["-ss", f"{covered:.3f}"] if covered > 0 else []) + ["-i", str(path)]
                procs = await self._start([], self._ffmpeg_cmd(inp, run_id, next_idx), sdir)
            else:
                run_base = max(covered, elapsed)
                procs = await self._start(self._streamlink_cmd(p["url"]),
                                          self._ffmpeg_cmd(["-fflags", "+genpts", "-i", "pipe:0"], run_id, next_idx),
                                          sdir)
            log.info("capture run started", extra={"session_id": sid, "run_id": run_id, "base": round(run_base, 2)})
            produced, next_idx, covered, reason = await self._watch(ctx, sid, sdir, procs, run_id, run_base,
                                                                    next_idx, covered, deadline)
            total_segments += produced
            if reason in ("cancelled", "stopped", "max_duration"):
                end_reason = reason
                break
            if p["platform"] == "file":
                if procs.returncode == 0:
                    end_reason = "source finished"
                    break
                failures += 1
            else:
                status = await asyncio.to_thread(probe_live, p["url"])
                if not status.live and not status.error:
                    end_reason = "stream offline"
                    break
                failures = 0 if produced > 0 else failures + 1
            if failures > self.settings.capture_reconnect_attempts:
                raise JobError(f"capture failed repeatedly: {procs.stderr_tail()}")
            backoff = min(60, 3 * (2 ** max(0, failures - 1)))
            log.warning("capture interrupted, reconnecting",
                        extra={"session_id": sid, "failures": failures, "backoff": backoff,
                               "stderr": procs.stderr_tail()[-300:]})
            for _ in range(backoff):
                ctx.check_cancel()
                await asyncio.sleep(1)

        await client.post(f"/internal/sessions/{sid}/end", {"reason": end_reason})
        return {"segments": total_segments, "covered_until": covered, "end_reason": end_reason}

    async def _start(self, sl_cmd: list[str], ff_cmd: list[str], cwd: Path) -> _Procs:
        procs = _Procs()
        if sl_cmd:
            r, w = os.pipe()
            procs.streamlink = await asyncio.create_subprocess_exec(
                *sl_cmd, stdout=w, stderr=asyncio.subprocess.PIPE, stdin=asyncio.subprocess.DEVNULL)
            procs.ffmpeg = await asyncio.create_subprocess_exec(
                *ff_cmd, stdin=r, stderr=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.DEVNULL, cwd=str(cwd))
            os.close(r)
            os.close(w)
            procs.drain(procs.streamlink)
        else:
            procs.ffmpeg = await asyncio.create_subprocess_exec(
                *ff_cmd, stdin=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.DEVNULL, cwd=str(cwd))
        procs.drain(procs.ffmpeg)
        return procs

    async def _watch(self, ctx: JobContext, sid: int, sdir: Path, procs: _Procs, run_id: int, run_base: float,
                     next_idx: int, covered: float, deadline: float) -> tuple[int, int, float, str]:
        csv_path = sdir / f"run_{run_id}.csv"
        seen = 0
        first_start: float | None = None
        produced = 0
        last_state = time.monotonic()
        last_evict = time.monotonic()
        reason = ""
        while True:
            done = procs.ffmpeg.returncode is not None
            rows = _read_csv(csv_path)
            new = rows[seen:]
            if new:
                batch = []
                for fname, s, e in new:
                    if first_start is None:
                        first_start = s
                    idx = int(Path(fname).stem.split("_")[-1])
                    fpath = sdir / fname
                    if not fpath.exists():
                        continue
                    start = run_base + (s - first_start)
                    seg = LocalSegment(idx=idx, run_id=run_id, start=round(start, 3), duration=round(e - s, 3),
                                       file=fname, size=fpath.stat().st_size,
                                       sha256=await asyncio.to_thread(sha256_file, fpath))
                    self.store.add(sid, seg)
                    batch.append({"idx": seg.idx, "run_id": run_id, "start": seg.start, "duration": seg.duration,
                                  "size": seg.size, "sha256": seg.sha256})
                    covered = max(covered, seg.end)
                    next_idx = max(next_idx, idx + 1)
                seen = len(rows)
                if batch:
                    produced += len(batch)
                    await ctx.client.post(f"/internal/sessions/{sid}/segments",
                                          {"base_url": self.public_url, "segments": batch})
            if done:
                break
            now = time.monotonic()
            if ctx.cancel.is_set():
                reason = "cancelled"
            elif time.time() > deadline:
                reason = "max_duration"
            elif now - last_state > 10:
                last_state = now
                try:
                    st = await ctx.client.get(f"/internal/sessions/{sid}/state")
                    if st["stop"]:
                        reason = "stopped"
                except InternalError as exc:
                    log.warning("state poll failed", extra={"err": str(exc)})
            if reason:
                await procs.terminate()
                continue  # loop once more to register the final segments
            if now - last_evict > 15:
                last_evict = now
                await self._evict(ctx, sid, covered)
            await asyncio.sleep(1.0)
        await procs.wait()
        return produced, next_idx, covered, reason

    async def _evict(self, ctx: JobContext, sid: int, covered: float) -> None:
        segs = self.store.list(sid)
        old = [s.idx for s in segs if s.end < covered - self.settings.buffer_seconds]
        force = False
        cap = self.settings.max_capture_disk_gb * (1024**3)
        total = await asyncio.to_thread(self.store.total_bytes)
        if total > cap:  # hard limit: drop the oldest 10% regardless of pins
            force = True
            old = [s.idx for s in segs[: max(1, len(segs) // 10)]]
            log.warning("capture disk cap reached, forcing eviction", extra={"bytes": total, "n": len(old)})
        if not old:
            return
        try:
            r = await ctx.client.post(f"/internal/sessions/{sid}/evict", {"idxs": old, "force": force})
        except InternalError as exc:
            log.warning("evict plan failed", extra={"err": str(exc)})
            return
        if r.get("allowed"):
            self.store.remove(sid, r["allowed"])


class _Procs:
    def __init__(self):
        self.streamlink: asyncio.subprocess.Process | None = None
        self.ffmpeg: asyncio.subprocess.Process | None = None
        self._err: deque[str] = deque(maxlen=40)
        self._tasks: list[asyncio.Task] = []

    def drain(self, proc: asyncio.subprocess.Process) -> None:
        async def _read():
            assert proc.stderr is not None
            while True:
                line = await proc.stderr.readline()
                if not line:
                    break
                self._err.append(line.decode(errors="replace").rstrip())
        self._tasks.append(asyncio.create_task(_read()))

    @property
    def returncode(self) -> int | None:
        return self.ffmpeg.returncode if self.ffmpeg else None

    def stderr_tail(self) -> str:
        return "\n".join(self._err)

    async def terminate(self) -> None:
        for p in (self.streamlink, self.ffmpeg):
            if p is not None and p.returncode is None:
                try:
                    p.terminate()
                except ProcessLookupError:
                    pass

    async def wait(self) -> None:
        for p in (self.ffmpeg, self.streamlink):
            if p is None:
                continue
            try:
                await asyncio.wait_for(p.wait(), timeout=15)
            except TimeoutError:
                p.kill()
                await p.wait()
        await asyncio.gather(*self._tasks, return_exceptions=True)


def _read_csv(path: Path) -> list[tuple[str, float, float]]:
    if not path.exists():
        return []
    rows = []
    text = path.read_text(encoding="utf-8", errors="replace")
    lines = text.splitlines()
    if lines and not text.endswith("\n"):
        lines = lines[:-1]  # last line still being written
    if lines:
        for row in csv.reader(lines):
            if len(row) >= 3:
                try:
                    rows.append((row[0], float(row[1]), float(row[2])))
                except ValueError:
                    continue
    return rows
