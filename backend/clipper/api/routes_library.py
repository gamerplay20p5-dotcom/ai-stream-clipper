"""Shared with me (multi-user stage 1): the ONLY area external accounts can reach.

Every request re-checks the share in the database (revocation, expiry, deactivation and role
changes take effect immediately). Unknown, not-shared, revoked or expired clips all answer 404:
an external account cannot learn whether a clip exists. Media is streamed by the API (Range
supported for the player), never through a permanent or guessable file URL.
"""

from __future__ import annotations

import re
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from .. import sharing, storage
from ..db import get_db
from ..models import Clip, ClipShare
from .deps import CurrentUser, audit, authenticated_user

router = APIRouter(prefix="/api/library", tags=["library"])
NO_STORE = {"Cache-Control": "private, no-store"}  # a revoked share must not keep playing from cache
NOT_FOUND = "não encontrado"


def _shared(db: Session, user: CurrentUser, clip_id: int) -> tuple[ClipShare | None, Clip]:
    """Admins see everything (share None); everyone else needs an ACTIVE share."""
    clip = db.get(Clip, clip_id)
    if clip is None or not clip.video_path:
        raise HTTPException(404, NOT_FOUND)
    if user.is_admin:
        return None, clip
    s = sharing.active_share(db, user.id, clip_id)
    if s is None:
        raise HTTPException(404, NOT_FOUND)
    return s, clip


def _file(rel: str) -> storage.Path:
    if not rel:
        raise HTTPException(404, NOT_FOUND)
    try:
        p = storage.resolve(rel)
    except ValueError as exc:
        raise HTTPException(404, NOT_FOUND) from exc
    if not p.is_file():
        raise HTTPException(404, NOT_FOUND)
    return p


@router.get("")
def library(db: Session = Depends(get_db), user: CurrentUser = Depends(authenticated_user)):
    """Clips shared with the authenticated account (its id comes from the session, never the request)."""
    return {"items": [sharing.public_clip(db, s, c) for s, c in sharing.shared_clips(db, user.id)]}


@router.get("/{clip_id}")
def item(clip_id: int, db: Session = Depends(get_db), user: CurrentUser = Depends(authenticated_user)):
    s, c = _shared(db, user, clip_id)
    if s is None:  # admin preview of what an external account would see
        s = ClipShare(clip_id=c.id, user_id=user.id, can_view=True, can_download=True, can_variants=True)
    return sharing.public_clip(db, s, c)


@router.get("/{clip_id}/video")
def video(clip_id: int, db: Session = Depends(get_db), user: CurrentUser = Depends(authenticated_user)):
    _s, c = _shared(db, user, clip_id)
    return FileResponse(_file(c.video_path), media_type="video/mp4", headers=NO_STORE)


@router.get("/{clip_id}/thumbnail")
def thumbnail(clip_id: int, db: Session = Depends(get_db), user: CurrentUser = Depends(authenticated_user)):
    _s, c = _shared(db, user, clip_id)
    return FileResponse(_file(c.thumb_path), media_type="image/jpeg", headers=NO_STORE)


@router.get("/{clip_id}/download")
def download(clip_id: int, request: Request, db: Session = Depends(get_db),
             user: CurrentUser = Depends(authenticated_user)):
    s, c = _shared(db, user, clip_id)
    if s is not None and not s.can_download:
        raise HTTPException(403, "download não liberado para este vídeo")
    slug = re.sub(r"[^a-z0-9]+", "-", (c.title or f"corte-{c.id}").lower()).strip("-")[:60] or f"corte-{c.id}"
    audit(db, request, user, "library.download", f"clip:{c.id}")
    db.commit()
    return FileResponse(_file(c.video_path), media_type="video/mp4", filename=f"{slug}-{c.id}.mp4", headers=NO_STORE)


@router.get("/{clip_id}/variants/{name}/video")
def variant_video(clip_id: int, name: Literal["edit", "original", "format"], db: Session = Depends(get_db),
                  user: CurrentUser = Depends(authenticated_user)):
    s, c = _shared(db, user, clip_id)
    if s is not None and not s.can_variants:
        raise HTTPException(404, NOT_FOUND)
    rec = ((c.provenance or {}).get("variants") or {}).get(name) or {}
    if rec.get("state") != "done":
        raise HTTPException(404, NOT_FOUND)
    return FileResponse(_file(rec.get("video_path", "")), media_type="video/mp4", headers=NO_STORE)
