"""Streamer voice (Phase 5, stage E): see who the system thinks is talking, listen, confirm."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import media, storage
from .. import pipeline_speakers as ps
from ..db import get_db
from ..models import Clip, SpeechTurn, Streamer, StreamSession
from .deps import CurrentUser, audit, current_user, require_admin

router = APIRouter(prefix="/api/speakers", tags=["speakers"])


class VoiceIn(BaseModel):
    session_id: int
    label: str = Field(pattern=r"^(streamer|other:\d{1,3})$")


def _streamer(db: Session, sid: int) -> Streamer:
    st = db.get(Streamer, sid)
    if st is None:
        raise HTTPException(404, "streamer not found")
    return st


@router.get("/streamers/{sid}")
def voice(sid: int, session_id: int | None = None, db: Session = Depends(get_db),
          _: CurrentUser = Depends(current_user)):
    _streamer(db, sid)
    if session_id is not None:
        sess = db.get(StreamSession, session_id)
        if sess is None or sess.streamer_id != sid:
            raise HTTPException(404, "session not found")
    return ps.overview(db, sid, session_id)


@router.post("/streamers/{sid}/confirm")
def confirm(sid: int, body: VoiceIn, request: Request, db: Session = Depends(get_db),
            user: CurrentUser = Depends(require_admin)):
    _check(db, sid, body.session_id)
    try:
        out = ps.confirm(db, sid, body.session_id, body.label)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    audit(db, request, user, "speaker.confirm", f"streamer:{sid}", {"session_id": body.session_id,
                                                                     "label": body.label})
    db.commit()
    return {"ok": True, **out}


@router.post("/streamers/{sid}/reject")
def reject(sid: int, body: VoiceIn, request: Request, db: Session = Depends(get_db),
           user: CurrentUser = Depends(require_admin)):
    _check(db, sid, body.session_id)
    try:
        out = ps.reject(db, sid, body.session_id, body.label)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    audit(db, request, user, "speaker.reject", f"streamer:{sid}", {"session_id": body.session_id,
                                                                    "label": body.label})
    db.commit()
    return {"ok": True, **out}


def _check(db: Session, sid: int, session_id: int) -> None:
    _streamer(db, sid)
    sess = db.get(StreamSession, session_id)
    if sess is None or sess.streamer_id != sid:
        raise HTTPException(404, "session not found")


@router.get("/turns/{tid}/audio")
def turn_audio(tid: int, db: Session = Depends(get_db), _: CurrentUser = Depends(current_user)):
    """The turn's audio cut from a stored clip source (only where a clip covers it)."""
    t = db.get(SpeechTurn, tid)
    if t is None:
        raise HTTPException(404, "turn not found")
    clip = db.scalar(select(Clip).where(Clip.session_id == t.session_id, Clip.source_path != "",
                                        Clip.source_start <= t.start,
                                        Clip.source_start + Clip.source_duration >= t.end).limit(1))
    if clip is None:
        raise HTTPException(404, "audio not available")
    try:
        src = storage.resolve(clip.source_path)
    except ValueError as exc:
        raise HTTPException(404, "audio not available") from exc
    if not src.is_file():
        raise HTTPException(404, "audio not available")
    a = max(0.0, t.start - clip.source_start - 0.2)
    dur = min(10.0, t.end - t.start + 0.4)
    try:
        data = media.run_bytes([media.FFMPEG, "-v", "error", "-ss", f"{a:.2f}", "-t", f"{dur:.2f}", "-i", str(src),
                                "-vn", "-ac", "1", "-ar", "24000", "-c:a", "libmp3lame", "-b:a", "64k",
                                "-f", "mp3", "pipe:1"], timeout=30)
    except media.MediaError as exc:
        raise HTTPException(502, "could not cut the audio") from exc
    return Response(data, media_type="audio/mpeg", headers={"Cache-Control": "private, max-age=600"})
