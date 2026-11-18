"""Media of evaluation examples: where to get it, where it is stored, retention and quota."""

from __future__ import annotations

import logging
from datetime import timedelta
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import storage
from ..db import utcnow
from ..models import Clip, EvalExample, Segment, StreamSession

log = logging.getLogger(__name__)


class NotAvailable(LookupError):
    pass


def _example(db: Session, eid: int) -> EvalExample:
    ex = db.get(EvalExample, eid)
    if ex is None:
        raise NotAvailable("example not found")
    return ex


def media_spec(db: Session, eid: int) -> dict:
    """Prefer the capture buffer (exact window); fall back to the clip source kept by the API."""
    ex = _example(db, eid)
    a, b = ex.media_start, ex.media_end
    sess = db.get(StreamSession, ex.session_id) if ex.session_id else None
    if sess is not None and sess.segment_base_url:
        segs = db.scalars(select(Segment).where(Segment.session_id == sess.id, Segment.available.is_(True),
                                                Segment.start < b, Segment.start + Segment.duration > a)
                          .order_by(Segment.idx)).all()
        if segs and segs[0].start <= a + 5 and segs[-1].start + segs[-1].duration >= b - 5:
            return {"source": "segments", "session_id": sess.id, "segment_base_url": sess.segment_base_url,
                    "range": [a, b]}
    clip = db.get(Clip, ex.clip_id) if ex.clip_id else None
    if clip is not None and clip.source_path:
        try:
            if storage.resolve(clip.source_path).is_file():
                lo = max(a, clip.source_start)
                hi = min(b, clip.source_start + clip.source_duration)
                return {"source": "clip", "source_start": clip.source_start, "range": [lo, hi]}
        except ValueError:
            pass
    raise NotAvailable("media no longer available (capture buffer rotated and no stored clip source)")


def clip_source_path(db: Session, eid: int) -> Path:
    ex = _example(db, eid)
    clip = db.get(Clip, ex.clip_id) if ex.clip_id else None
    if clip is None or not clip.source_path:
        raise NotAvailable("no clip source")
    p = storage.resolve(clip.source_path)
    if not p.is_file():
        raise NotAvailable("clip source file missing")
    return p


def dataset_dir() -> Path:
    d = storage.root() / "dataset"
    d.mkdir(parents=True, exist_ok=True)
    return d


def media_dest(db: Session, eid: int) -> Path:
    _example(db, eid)
    return dataset_dir() / f"example_{int(eid)}.mp4"


def store_media(db: Session, eid: int, path: Path, size: int, start: float, duration: float) -> None:
    ex = _example(db, eid)
    ex.media_path = storage.rel(path)
    ex.media_bytes = int(size)
    ex.media_start = round(float(start), 2)
    ex.media_end = round(float(start) + float(duration), 2)
    ex.media_state = "ready"
    ex.media_error = ""


def mark_failed(db: Session, eid: int, error: str) -> None:
    ex = db.get(EvalExample, eid)
    if ex is not None:
        ex.media_state = "failed"
        ex.media_error = error[:1000]


def media_file(ex: EvalExample) -> Path:
    if ex.media_state != "ready" or not ex.media_path:
        raise NotAvailable("media not ready")
    p = storage.resolve(ex.media_path)
    if not p.is_file():
        raise NotAvailable("media file missing")
    return p


def _drop(ex: EvalExample, reason: str) -> int:
    freed = 0
    try:
        p = storage.resolve(ex.media_path) if ex.media_path else None
        if p is not None and p.is_file():
            freed = p.stat().st_size
            p.unlink()
    except (OSError, ValueError) as exc:
        log.warning("could not delete example media", extra={"example_id": ex.id, "err": str(exc)})
    ex.media_state, ex.media_path, ex.media_bytes, ex.media_error = "expired", "", 0, reason
    return freed


def enforce_retention(db: Session, settings) -> dict:
    """Delete example media by age and quota. Labels and snapshots are always kept."""
    now = utcnow()
    out = {"expired": 0, "freed_bytes": 0}
    ready = list(db.scalars(select(EvalExample).where(EvalExample.media_state == "ready")
                            .order_by(EvalExample.created_at)))
    for ex in ready:
        days = settings.dataset_reviewed_retention_days if ex.reviewed_at else settings.dataset_retention_days
        if days > 0 and ex.created_at < now - timedelta(days=days):
            out["freed_bytes"] += _drop(ex, f"retenção de {days} dias")
            out["expired"] += 1
    quota = int(settings.dataset_quota_gb * 1024**3)
    alive = [e for e in ready if e.media_state == "ready"]
    total = sum(e.media_bytes for e in alive)
    # over quota: unreviewed first (oldest first), then reviewed
    for ex in sorted(alive, key=lambda e: (e.reviewed_at is not None, e.created_at)):
        if total <= quota:
            break
        total -= ex.media_bytes
        out["freed_bytes"] += _drop(ex, "cota do dataset atingida")
        out["expired"] += 1
    return out
