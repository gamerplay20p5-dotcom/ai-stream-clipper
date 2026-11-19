"""Operational safety for a rollback across migration 0010 (publication media snapshots).

Older code sends ``clip.video_path`` (the clip's CURRENT video) instead of the publication's
snapshot. Before rolling back: pause publishing, then list pending publications whose approved
media differs from the clip's current media; cancel those (a person re-creates them later) or keep
publishing paused until the version with snapshots is back.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import Clip, Publication
from . import service

PENDING = ("draft", "proposed", "scheduled", "uploading", "processing", "uncertain")
CANCELLABLE = ("draft", "proposed", "scheduled")


def check(db: Session) -> dict:
    """Pending publications that an older version would send with DIFFERENT media than approved."""
    rows = []
    for p in db.scalars(select(Publication).where(Publication.status.in_(PENDING))):
        clip = db.get(Clip, p.clip_id) if p.clip_id else None
        current_path, current_sha = (clip.video_path, clip.sha256) if clip else ("", "")
        approved_path = getattr(p, "video_path", "") or ""
        differs = bool((approved_path and approved_path != current_path) or
                       (p.video_sha256 and current_sha and p.video_sha256 != current_sha))
        if differs:
            rows.append({"publication_id": p.id, "clip_id": p.clip_id, "status": p.status, "platform": p.platform,
                         "approved": approved_path, "current": current_path,
                         "cancellable": p.status in CANCELLABLE})
    from .editorial import control

    return {"paused": bool(control(db).get("paused")), "mismatched": rows,
            "safe_to_rollback": bool(control(db).get("paused")) and not any(not r["cancellable"] for r in rows)}


def cancel_mismatched(db: Session, user: str) -> list[int]:
    """Cancel (never delete) pending publications whose approved media is no longer the clip's."""
    done = []
    for r in check(db)["mismatched"]:
        if not r["cancellable"]:
            continue
        p = db.get(Publication, r["publication_id"])
        p.status = "cancelled"
        service.log_event(p, "cancelled", f"rollback: a mídia aprovada difere da atual (por {user})")
        done.append(p.id)
    db.flush()
    return done
