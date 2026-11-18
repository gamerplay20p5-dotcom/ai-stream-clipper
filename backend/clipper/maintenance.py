"""Periodic maintenance run by the API process (task scheduler):

* expire job leases of lost workers (retry / dead-letter)
* mark silent workers offline
* continue clips without vision when no vision worker answers in time
* retention policy for clip files and raw analysis data
"""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import timedelta

from sqlalchemy import delete, select, text

from . import pipeline, queue, storage
from .db import is_postgres, session_scope, utcnow
from .models import AudioFeature, Clip, Segment, StreamSession, TranscriptWord
from .settings_store import load_ai_settings

log = logging.getLogger(__name__)

ADVISORY_KEY = 7_420_151


def tick() -> dict:
    out = {"expired": 0, "offline": 0, "vision_fallback": 0}
    with session_scope() as db:
        if is_postgres():
            got = db.execute(text("SELECT pg_try_advisory_xact_lock(:k)"), {"k": ADVISORY_KEY}).scalar()
            if not got:
                return out  # another API replica runs maintenance
        expired = queue.expire_leases(db)
        for j in expired:
            if j.status == "dead":
                pipeline.on_job_dead(db, j)
        out["expired"] = len(expired)
        out["offline"] = pipeline.mark_stale_workers(db)
        out["vision_fallback"] = pipeline.vision_timeouts(db)
    return out


def retention() -> dict:
    removed = {"clips": 0, "raw_sessions": 0}
    with session_scope() as db:
        s = load_ai_settings(db)
        now = utcnow()
        rules = [("rejected", s.retention_rejected_days), ("ready", s.retention_unreviewed_days),
                 ("failed", s.retention_unreviewed_days), ("approved", s.retention_approved_days)]
        doomed: list[int] = []
        for status, days in rules:
            if days <= 0:
                continue
            cutoff = now - timedelta(days=days)
            for c in db.scalars(select(Clip).where(Clip.status == status, Clip.updated_at < cutoff)):
                doomed.append(c.id)
                db.delete(c)
        # raw analysis data of sessions that ended more than 30 days ago
        old = [sid for (sid,) in db.execute(select(StreamSession.id).where(
            StreamSession.status != "live", StreamSession.ended_at < now - timedelta(days=30)))]
        for sid in old:
            for model in (TranscriptWord, AudioFeature, Segment):
                db.execute(delete(model).where(model.session_id == sid))
        removed["raw_sessions"] = len(old)
    for cid in doomed:
        storage.delete_clip_files(cid)
    removed["clips"] = len(doomed)
    if any(removed.values()):
        log.info("retention cleanup", extra=removed)
    return removed


async def loop(interval: float) -> None:
    last_retention = 0.0
    while True:
        try:
            await asyncio.to_thread(tick)
            if time.monotonic() - last_retention > 3600:
                last_retention = time.monotonic()
                await asyncio.to_thread(retention)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("maintenance tick failed")
        await asyncio.sleep(interval)
