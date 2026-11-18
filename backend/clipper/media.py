"""FFmpeg / ffprobe helpers. Commands are always built as argument lists (no shell)."""

from __future__ import annotations

import asyncio
import json
import logging
import shutil
import subprocess
from pathlib import Path

log = logging.getLogger(__name__)

FFMPEG = shutil.which("ffmpeg") or "ffmpeg"
FFPROBE = shutil.which("ffprobe") or "ffprobe"


class MediaError(RuntimeError):
    pass


async def run(args: list[str], *, cwd: Path | None = None, timeout: float = 1800) -> str:
    proc = await asyncio.create_subprocess_exec(
        *args,
        cwd=str(cwd) if cwd else None,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        out, err = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except TimeoutError:
        proc.kill()
        await proc.wait()
        raise MediaError(f"{Path(args[0]).name} timed out after {timeout}s")
    if proc.returncode != 0:
        tail = err.decode(errors="replace")[-1500:]
        raise MediaError(f"{Path(args[0]).name} exited {proc.returncode}: {tail}")
    return out.decode(errors="replace")


def run_sync(args: list[str], *, cwd: Path | None = None, timeout: float = 1800) -> str:
    p = subprocess.run(args, cwd=str(cwd) if cwd else None, capture_output=True, timeout=timeout)
    if p.returncode != 0:
        raise MediaError(f"{Path(args[0]).name} exited {p.returncode}: {p.stderr.decode(errors='replace')[-1500:]}")
    return p.stdout.decode(errors="replace")


async def probe(path: Path) -> dict:
    out = await run([FFPROBE, "-v", "error", "-print_format", "json", "-show_format", "-show_streams", str(path)],
                    timeout=60)
    return json.loads(out)


def probe_sync(path: Path) -> dict:
    out = run_sync([FFPROBE, "-v", "error", "-print_format", "json", "-show_format", "-show_streams", str(path)],
                   timeout=60)
    return json.loads(out)


def video_stream(info: dict) -> dict | None:
    return next((s for s in info.get("streams", []) if s.get("codec_type") == "video"), None)


def audio_stream(info: dict) -> dict | None:
    return next((s for s in info.get("streams", []) if s.get("codec_type") == "audio"), None)


def duration_of(info: dict) -> float:
    try:
        return float(info.get("format", {}).get("duration") or 0.0)
    except (TypeError, ValueError):
        return 0.0


async def extract_wav(src: Path, dst: Path, sr: int = 16000, threads: int = 2) -> bool:
    """Mono 16 kHz PCM. Returns False when the source has no audio stream."""
    info = await probe(src)
    if audio_stream(info) is None:
        return False
    await run([FFMPEG, "-hide_banner", "-loglevel", "error", "-y", "-threads", str(threads), "-i", str(src),
               "-map", "0:a:0", "-vn", "-ac", "1", "-ar", str(sr), "-c:a", "pcm_s16le", str(dst)], timeout=300)
    return True


async def concat_copy(parts: list[Path], dst: Path, workdir: Path) -> None:
    """Join MPEG-TS segments without re-encoding into an MP4."""
    lst = workdir / f"{dst.stem}.concat.txt"
    lst.write_text("".join(f"file '{p.resolve().as_posix()}'\n" for p in parts), encoding="utf-8")
    try:
        await run([FFMPEG, "-hide_banner", "-loglevel", "error", "-y", "-f", "concat", "-safe", "0", "-i", str(lst),
                   "-map", "0:v:0?", "-map", "0:a:0?", "-c", "copy", "-movflags", "+faststart", str(dst)], timeout=600)
    finally:
        lst.unlink(missing_ok=True)
