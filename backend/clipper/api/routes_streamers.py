from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .. import queue, storage
from ..config import get_settings
from ..db import get_db, utcnow
from ..models import Clip, Event, Job, Streamer, StreamSession
from ..pipeline import active_session
from ..platforms import InvalidStreamURL, VideoUnavailable, canonical_youtube, parse_file_source, parse_stream_url
from . import serializers as ser
from .deps import CurrentUser, audit, current_user, require_admin

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api", tags=["streamers"])

ALLOWED_PREF_KEYS = {"game", "min_score", "caption_style", "layout", "webcam_region", "language", "notes",
                     "republish_allowed", "credit", "visual_discovery"}  # Phase 4: rights to republish + credit line


class StreamerIn(BaseModel):
    url: str = Field(max_length=512)
    display_name: str = Field("", max_length=255)
    preferences: dict = Field(default_factory=dict)


class StreamerPatch(BaseModel):
    enabled: bool | None = None
    display_name: str | None = Field(None, max_length=255)
    preferences: dict | None = None


def _clean_prefs(prefs: dict) -> dict:
    out = {k: v for k, v in (prefs or {}).items() if k in ALLOWED_PREF_KEYS}
    if "webcam_region" in out and out["webcam_region"] is not None:
        r = out["webcam_region"]
        try:
            out["webcam_region"] = {k: min(1.0, max(0.0, float(r[k]))) for k in ("x", "y", "w", "h")}
        except (KeyError, TypeError, ValueError) as exc:
            raise HTTPException(422, "webcam_region must have numeric x,y,w,h in 0..1") from exc
    if "game" in out:
        out["game"] = str(out["game"])[:60]
    # Per-channel pilot of the visual discovery (None = follow the global setting)
    if "visual_discovery" in out and out["visual_discovery"] not in (None, "off", "shadow", "on"):
        raise HTTPException(422, "visual_discovery must be off, shadow, on or null")
    return out


@router.get("/streamers")
def list_streamers(db: Session = Depends(get_db), _: CurrentUser = Depends(current_user)):
    counts = dict(db.execute(select(Clip.streamer_id, func.count()).group_by(Clip.streamer_id)).all())
    out = []
    for s in db.scalars(select(Streamer).order_by(Streamer.created_at.desc())):
        out.append(ser.streamer(s, active_session(db, s.id), counts.get(s.id, 0)))
    return out


@router.post("/streamers", status_code=201)
async def add_streamer(body: StreamerIn, request: Request, db: Session = Depends(get_db),
                       user: CurrentUser = Depends(require_admin)):
    cfg = get_settings()
    raw = body.url.strip()
    try:
        if raw.startswith("file://"):
            if not cfg.allow_file_sources:
                raise InvalidStreamURL("file sources are disabled (ASC_ALLOW_FILE_SOURCES)")
            ref = parse_file_source(raw, cfg.test_media_dir, must_exist=cfg.test_media_dir.is_dir())
        else:
            ref = await asyncio.to_thread(parse_stream_url, raw)
            if ref.platform == "youtube" and ref.channel.startswith("v:"):
                try:  # a watch?v= link is one broadcast; monitor the channel so the next lives are found
                    ref = await asyncio.to_thread(canonical_youtube, ref)
                except VideoUnavailable:
                    raise
                except InvalidStreamURL as exc:  # page unreadable now: keep the link, the monitor retries
                    log.warning("youtube channel not resolved at registration", extra={"err": str(exc)})
    except InvalidStreamURL as exc:
        raise HTTPException(422, str(exc)) from exc
    existing = db.scalar(select(Streamer).where(Streamer.platform == ref.platform, Streamer.channel == ref.channel))
    if existing is not None:
        if ref.platform == "file" and not existing.enabled:
            existing.enabled = True  # re-arm a one-shot test video
            existing.check_requested = True
            audit(db, request, user, "streamer.rearm", f"streamer:{existing.id}")
            db.commit()
            return ser.streamer(existing)
        raise HTTPException(409, "this channel is already registered")
    s = Streamer(platform=ref.platform, channel=ref.channel, url=ref.url,
                 display_name=body.display_name.strip() or ref.display_name,
                 preferences=_clean_prefs(body.preferences), status="unknown", check_requested=True)
    db.add(s)
    db.flush()
    audit(db, request, user, "streamer.create", f"streamer:{s.id}", {"url": ref.url})
    db.commit()
    return ser.streamer(s)


@router.get("/streamers/{sid}")
def get_streamer(sid: int, db: Session = Depends(get_db), _: CurrentUser = Depends(current_user)):
    s = db.get(Streamer, sid)
    if s is None:
        raise HTTPException(404, "streamer not found")
    sessions = db.scalars(select(StreamSession).where(StreamSession.streamer_id == sid)
                          .order_by(StreamSession.id.desc()).limit(50)).all()
    n = db.scalar(select(func.count()).select_from(Clip).where(Clip.streamer_id == sid))
    out = ser.streamer(s, active_session(db, sid), n or 0)
    out["sessions"] = [ser.session(x) for x in sessions]
    return out


@router.patch("/streamers/{sid}")
def patch_streamer(sid: int, body: StreamerPatch, request: Request, db: Session = Depends(get_db),
                   user: CurrentUser = Depends(require_admin)):
    s = db.get(Streamer, sid)
    if s is None:
        raise HTTPException(404, "streamer not found")
    changes = body.model_dump(exclude_none=True)
    if body.display_name is not None:
        s.display_name = body.display_name.strip()
    if body.preferences is not None:
        s.preferences = _clean_prefs({**(s.preferences or {}), **body.preferences})
    if body.enabled is not None:
        s.enabled = body.enabled
        if body.enabled:
            s.check_requested = True
        else:
            sess = active_session(db, sid)
            if sess:
                sess.stop_requested = True
    audit(db, request, user, "streamer.update", f"streamer:{sid}", changes)
    db.commit()
    return ser.streamer(s, active_session(db, sid))


@router.post("/streamers/{sid}/check")
def check_now(sid: int, db: Session = Depends(get_db), _: CurrentUser = Depends(require_admin)):
    s = db.get(Streamer, sid)
    if s is None:
        raise HTTPException(404, "streamer not found")
    s.check_requested = True
    db.commit()
    # server clock: the panel only calls the request done when a result is recorded AFTER this instant
    return {"ok": True, "requested_at": ser.iso(utcnow()), "check_requested": True}


@router.delete("/streamers/{sid}")
def delete_streamer(sid: int, request: Request, db: Session = Depends(get_db),
                    user: CurrentUser = Depends(require_admin)):
    s = db.get(Streamer, sid)
    if s is None:
        raise HTTPException(404, "streamer not found")
    session_ids = [x.id for x in s.sessions]
    if session_ids:
        for j in db.scalars(select(Job).where(Job.status.in_(("queued", "running")))):
            if (j.payload or {}).get("session_id") in session_ids or (j.payload or {}).get("streamer_id") == sid:
                queue.request_cancel(db, j)
    clip_ids = [c for (c,) in db.execute(select(Clip.id).where(Clip.streamer_id == sid))]
    db.delete(s)
    audit(db, request, user, "streamer.delete", f"streamer:{sid}", {"channel": s.channel, "clips": len(clip_ids)})
    db.commit()
    for cid in clip_ids:
        storage.delete_clip_files(cid)
    return {"ok": True}


class TestClipIn(BaseModel):
    mode: str = Field(pattern="^(last30|last60|last90|custom)$")
    start: float | None = Field(None, ge=0)
    end: float | None = Field(None, ge=0)


@router.post("/streamers/{sid}/test-clip", status_code=201)
def test_clip(sid: int, body: TestClipIn, request: Request, db: Session = Depends(get_db),
              user: CurrentUser = Depends(require_admin)):
    """TESTE MANUAL: real pipeline on a buffer range; scoring never blocks it."""
    from .. import diagnostics as diag
    from . import serializers as s2

    try:
        clip = diag.create_test_clip(db, sid, body.mode, body.start, body.end, user.email)
    except diag.TestClipError as exc:
        raise HTTPException(exc.status, str(exc)) from exc
    audit(db, request, user, "clip.test_create", f"clip:{clip.id}", {"mode": body.mode, "start": clip.start,
                                                                       "end": clip.end})
    db.commit()
    return s2.clip(clip, db.get(Streamer, sid))


@router.get("/sessions")
def list_sessions(status: str | None = None, limit: int = 50, db: Session = Depends(get_db),
                  _: CurrentUser = Depends(current_user)):
    q = select(StreamSession).order_by(StreamSession.id.desc()).limit(min(200, max(1, limit)))
    if status:
        q = q.where(StreamSession.status == status)
    return [ser.session(x) for x in db.scalars(q)]


@router.get("/sessions/{sid}")
def get_session(sid: int, db: Session = Depends(get_db), _: CurrentUser = Depends(current_user)):
    sess = db.get(StreamSession, sid)
    if sess is None:
        raise HTTPException(404, "session not found")
    events = db.scalars(select(Event).where(Event.session_id == sid).order_by(Event.t.desc()).limit(200)).all()
    out = ser.session(sess)
    out["events"] = [{"t": e.t, "kind": e.kind, "strength": e.strength, "duration": e.duration, "data": e.data}
                     for e in events]
    return out


@router.post("/sessions/{sid}/stop")
def stop_session(sid: int, request: Request, db: Session = Depends(get_db), user: CurrentUser = Depends(require_admin)):
    sess = db.get(StreamSession, sid)
    if sess is None:
        raise HTTPException(404, "session not found")
    sess.stop_requested = True
    audit(db, request, user, "session.stop", f"session:{sid}")
    db.commit()
    return {"ok": True}


@router.get("/test-media")
def list_test_media(_: CurrentUser = Depends(require_admin)):
    cfg = get_settings()
    if not cfg.allow_file_sources or not cfg.test_media_dir.is_dir():
        return {"enabled": cfg.allow_file_sources, "files": []}
    files = sorted(p.name for p in cfg.test_media_dir.iterdir()
                   if p.is_file() and p.suffix.lower() in {".mp4", ".mkv", ".ts", ".mov", ".webm", ".flv"})
    return {"enabled": True, "files": files}
