from __future__ import annotations

import re
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .. import storage
from ..db import get_db
from ..models import Clip, Streamer
from ..pipeline import enqueue_render
from ..settings_store import load_ai_settings
from . import serializers as ser
from .deps import CurrentUser, audit, current_user, require_admin

router = APIRouter(prefix="/api/clips", tags=["clips"])


class ClipPatch(BaseModel):
    title: str | None = Field(None, max_length=255)
    description: str | None = Field(None, max_length=5000)
    start: float | None = Field(None, ge=0)
    end: float | None = Field(None, ge=0)
    caption_style: Literal["bold", "clean", "boxed", "none"] | None = None
    layout: Literal["auto", "gameplay", "split", "zoom"] | None = None
    webcam_region: dict | None = None


def _get(db: Session, cid: int) -> Clip:
    c = db.get(Clip, cid)
    if c is None:
        raise HTTPException(404, "clip not found")
    return c


@router.get("")
def list_clips(status: str | None = None, streamer_id: int | None = None, sort: str = "recent",
               limit: int = 60, offset: int = 0, db: Session = Depends(get_db), _: CurrentUser = Depends(current_user)):
    q = select(Clip)
    cq = select(func.count()).select_from(Clip)
    if status:
        statuses = [s for s in status.split(",") if s]
        q = q.where(Clip.status.in_(statuses))
        cq = cq.where(Clip.status.in_(statuses))
    if streamer_id:
        q = q.where(Clip.streamer_id == streamer_id)
        cq = cq.where(Clip.streamer_id == streamer_id)
    q = q.order_by(Clip.score.desc(), Clip.id.desc()) if sort == "score" else q.order_by(Clip.id.desc())
    q = q.limit(min(200, max(1, limit))).offset(max(0, offset))
    clips = db.scalars(q).all()
    streamers = {s.id: s for s in db.scalars(select(Streamer).where(Streamer.id.in_({c.streamer_id for c in clips})))}
    return {"total": db.scalar(cq) or 0, "items": [ser.clip(c, streamers.get(c.streamer_id)) for c in clips]}


@router.get("/{cid}")
def get_clip(cid: int, db: Session = Depends(get_db), _: CurrentUser = Depends(current_user)):
    c = _get(db, cid)
    return ser.clip(c, db.get(Streamer, c.streamer_id))


@router.patch("/{cid}")
def patch_clip(cid: int, body: ClipPatch, request: Request, db: Session = Depends(get_db),
               user: CurrentUser = Depends(require_admin)):
    c = _get(db, cid)
    settings = load_ai_settings(db)
    data = body.model_dump(exclude_unset=True)
    start = data.get("start", c.start)
    end = data.get("end", c.end)
    if end - start < 3 or end - start > settings.max_clip_seconds * 2:
        raise HTTPException(422, f"duration must be between 3 and {settings.max_clip_seconds * 2:.0f} seconds")
    if "webcam_region" in data and data["webcam_region"] is not None:
        try:
            data["webcam_region"] = {k: min(1.0, max(0.0, float(data["webcam_region"][k]))) for k in "xywh"}
        except (KeyError, TypeError, ValueError) as exc:
            raise HTTPException(422, "webcam_region needs x,y,w,h") from exc
    if ("start" in data or "end" in data) and (start != c.start or end != c.end):
        from ..evaluation.feedback import record_time_edit

        record_time_edit(c, start, end, user.email)  # human ideal bounds (used as a label)
    for k, v in data.items():
        setattr(c, k, v)
    audit(db, request, user, "clip.update", f"clip:{cid}", {k: v for k, v in data.items() if k != "description"})
    db.commit()
    return ser.clip(c)


def _human_label(db: Session, clip: Clip, user: str, action: str) -> None:
    """The owner's approve/reject is a human label for continuous learning (never blocks the action)."""
    from ..evaluation.feedback import record_clip_action

    try:
        with db.begin_nested():
            record_clip_action(db, clip, user, action)
    except Exception:
        import logging

        logging.getLogger(__name__).exception("could not record the human label")


@router.post("/{cid}/rerender")
def rerender(cid: int, request: Request, db: Session = Depends(get_db), user: CurrentUser = Depends(require_admin)):
    c = _get(db, cid)
    if c.status in ("rendering", "analyzing", "candidate"):
        raise HTTPException(409, "clip is already being processed")
    c.version += 1
    enqueue_render(db, c)
    audit(db, request, user, "clip.rerender", f"clip:{cid}", {"version": c.version})
    db.commit()
    return ser.clip(c)


@router.post("/{cid}/reanalyze")
def reanalyze(cid: int, request: Request, db: Session = Depends(get_db), user: CurrentUser = Depends(require_admin)):
    """Manual reprocessing: run the multimodal analysis + director again (bounds restart
    from the heuristic candidate). Editing without re-analysis is ``/rerender``."""
    c = _get(db, cid)
    if c.status in ("rendering", "analyzing", "candidate"):
        raise HTTPException(409, "clip is already being processed")
    from ..pipeline_ai import reanalyze as do_reanalyze

    do_reanalyze(db, c)
    audit(db, request, user, "clip.reanalyze", f"clip:{cid}", {"run": c.analysis_runs})
    db.commit()
    return ser.clip(c)


@router.post("/{cid}/approve")
def approve(cid: int, request: Request, db: Session = Depends(get_db), user: CurrentUser = Depends(require_admin)):
    c = _get(db, cid)
    if c.status not in ("ready", "rejected", "approved"):
        raise HTTPException(409, "only rendered clips can be approved")
    c.status = "approved"
    audit(db, request, user, "clip.approve", f"clip:{cid}")
    _human_label(db, c, user.email, "approve")
    db.commit()
    return ser.clip(c)


@router.post("/{cid}/reject")
def reject(cid: int, request: Request, db: Session = Depends(get_db), user: CurrentUser = Depends(require_admin)):
    c = _get(db, cid)
    if c.status in ("rendering", "analyzing"):
        raise HTTPException(409, "clip is being processed")
    c.status = "rejected"
    audit(db, request, user, "clip.reject", f"clip:{cid}")
    _human_label(db, c, user.email, "reject")
    db.commit()
    return ser.clip(c)


@router.delete("/{cid}")
def delete_clip(cid: int, request: Request, db: Session = Depends(get_db), user: CurrentUser = Depends(require_admin)):
    c = _get(db, cid)
    db.delete(c)
    audit(db, request, user, "clip.delete", f"clip:{cid}", {"title": c.title})
    db.commit()
    storage.delete_clip_files(cid)
    return {"ok": True}


def _file(rel: str) -> storage.Path:
    if not rel:
        raise HTTPException(404, "file not available")
    p = storage.resolve(rel)
    if not p.is_file():
        raise HTTPException(404, "file missing")
    return p


@router.get("/{cid}/video")
def video(cid: int, db: Session = Depends(get_db), _: CurrentUser = Depends(current_user)):
    c = _get(db, cid)
    return FileResponse(_file(c.video_path), media_type="video/mp4",
                        headers={"Cache-Control": "private, max-age=60"})


@router.get("/{cid}/thumbnail")
def thumbnail(cid: int, db: Session = Depends(get_db), _: CurrentUser = Depends(current_user)):
    c = _get(db, cid)
    return FileResponse(_file(c.thumb_path), media_type="image/jpeg",
                        headers={"Cache-Control": "private, max-age=300"})


@router.get("/{cid}/download")
def download(cid: int, request: Request, db: Session = Depends(get_db), user: CurrentUser = Depends(current_user)):
    c = _get(db, cid)
    slug = re.sub(r"[^a-z0-9]+", "-", (c.title or f"clip-{cid}").lower()).strip("-")[:60] or f"clip-{cid}"
    audit(db, request, user, "clip.download", f"clip:{cid}")
    db.commit()
    return FileResponse(_file(c.video_path), media_type="video/mp4", filename=f"{slug}-{cid}.mp4")
