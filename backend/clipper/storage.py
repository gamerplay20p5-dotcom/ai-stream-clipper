"""Storage manager for API-side media (clip sources, renders, thumbnails)."""

from __future__ import annotations

import hashlib
import os
import shutil
from collections.abc import AsyncIterator
from pathlib import Path

from .config import get_settings


class StorageFull(RuntimeError):
    pass


def root() -> Path:
    p = get_settings().storage_dir
    p.mkdir(parents=True, exist_ok=True)
    return p


def clip_dir(clip_id: int) -> Path:
    d = root() / "clips" / str(int(clip_id))
    d.mkdir(parents=True, exist_ok=True)
    return d


def resolve(rel: str) -> Path:
    """Resolve a stored relative path, refusing anything outside the storage root."""
    base = root().resolve()
    p = (base / rel).resolve()
    if base != p and base not in p.parents:
        raise ValueError("path escapes storage root")
    return p


def rel(path: Path) -> str:
    return path.resolve().relative_to(root().resolve()).as_posix()


def usage_bytes() -> int:
    total = 0
    for dirpath, _, files in os.walk(root()):
        for f in files:
            try:
                total += os.path.getsize(os.path.join(dirpath, f))
            except OSError:
                pass
    return total


def disk_info() -> dict:
    du = shutil.disk_usage(root())
    used = usage_bytes()
    limit = int(get_settings().storage_max_gb * (1024**3))
    return {"used_bytes": used, "limit_bytes": limit, "disk_free_bytes": du.free, "disk_total_bytes": du.total}


async def save_stream(chunks: AsyncIterator[bytes], dest: Path, max_bytes: int, expected_sha: str) -> int:
    """Stream a request body to ``dest``; verify SHA-256 before the atomic rename.
    Returns the size in bytes."""
    info = disk_info()
    if info["used_bytes"] + max_bytes > info["limit_bytes"] or info["disk_free_bytes"] < 2 * max_bytes:
        raise StorageFull("storage limit reached")
    tmp = dest.with_suffix(dest.suffix + ".part")
    h = hashlib.sha256()
    size = 0
    try:
        with open(tmp, "wb") as f:
            async for chunk in chunks:
                if not chunk:
                    continue
                size += len(chunk)
                if size > max_bytes:
                    raise ValueError("upload too large")
                h.update(chunk)
                f.write(chunk)
        if not expected_sha or h.hexdigest() != expected_sha:
            raise ValueError("integrity check failed (sha256 mismatch)")
        os.replace(tmp, dest)
    finally:
        tmp.unlink(missing_ok=True)
    return size


def delete_clip_files(clip_id: int) -> None:
    d = root() / "clips" / str(int(clip_id))
    if d.exists():
        shutil.rmtree(d, ignore_errors=True)
