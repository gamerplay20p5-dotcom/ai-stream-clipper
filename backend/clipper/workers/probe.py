"""Live probes isolated in a child process, with a hard deadline.

`asyncio.wait_for(asyncio.to_thread(probe_live, ...))` cannot stop a thread blocked inside streamlink, DNS or a
socket: the thread keeps running (and piles up) after the timeout. A child process can be killed, so a stuck probe
never outlives its deadline and never holds resources of the monitor or of the captures in the same worker.

Run directly: ``python -m clipper.workers.probe <url> <http_timeout_s>`` prints one JSON line.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import time
from dataclasses import asdict, dataclass

ERR_TIMEOUT = "verificação excedeu o tempo limite"
ERR_CRASH = "verificação terminou sem resposta"


@dataclass
class ProbeResult:
    live: bool
    title: str = ""
    error: str = ""
    note: str = ""
    channel_id: str = ""
    elapsed_s: float = 0.0
    timed_out: bool = False


def _command(url: str, http_timeout: float) -> list[str]:
    return [sys.executable, "-m", "clipper.workers.probe", url, str(http_timeout)]


async def probe_isolated(url: str, deadline_s: float, http_timeout_s: float = 20.0,
                         command: list[str] | None = None) -> ProbeResult:
    """Probe `url` in a child process. Never takes longer than `deadline_s` (+ the time to reap the child)."""
    started = time.monotonic()
    proc = await asyncio.create_subprocess_exec(
        *(command or _command(url, http_timeout_s)), stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL, env={**os.environ, "PYTHONUNBUFFERED": "1"})
    try:
        out, _ = await asyncio.wait_for(proc.communicate(), timeout=deadline_s)
    except asyncio.TimeoutError:
        _kill(proc)
        await _reap(proc)
        return ProbeResult(False, error=ERR_TIMEOUT, elapsed_s=round(time.monotonic() - started, 1), timed_out=True)
    except BaseException:  # cancelled (monitor restart / shutdown): never leave the child behind
        _kill(proc)
        await _reap(proc)
        raise
    elapsed = round(time.monotonic() - started, 1)
    try:
        data = json.loads(out.decode("utf-8", "replace").strip().splitlines()[-1])
        return ProbeResult(bool(data.get("live")), str(data.get("title", ""))[:500], str(data.get("error", ""))[:1000],
                           str(data.get("note", ""))[:1000], str(data.get("channel_id", ""))[:64], elapsed)
    except (ValueError, IndexError, AttributeError):
        return ProbeResult(False, error=f"{ERR_CRASH} (código {proc.returncode})", elapsed_s=elapsed)


def _kill(proc: asyncio.subprocess.Process) -> None:
    if proc.returncode is None:
        try:
            proc.kill()
        except ProcessLookupError:
            pass


async def _reap(proc: asyncio.subprocess.Process) -> None:
    try:
        await asyncio.wait_for(proc.wait(), timeout=10)
    except asyncio.TimeoutError:  # pragma: no cover - a killed process that does not exit is an OS problem
        pass


def main(argv: list[str]) -> int:
    from ..platforms import probe_live

    url, http_timeout = argv[0], float(argv[1]) if len(argv) > 1 else 20.0
    try:
        st = probe_live(url, timeout=http_timeout)
        payload = {"live": st.live, "title": st.title, "error": st.error, "note": st.note, "channel_id": st.channel_id}
    except Exception as exc:  # report, never crash silently
        payload = {"live": False, "error": f"{type(exc).__name__}: {str(exc)[:300]}"}
    sys.stdout.write(json.dumps(payload) + "\n")
    sys.stdout.flush()
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main(sys.argv[1:]))


__all__ = ["ProbeResult", "probe_isolated", "ERR_TIMEOUT", "ERR_CRASH", "asdict"]
