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
ACTIVE_SEND = ("uploading", "processing")


def check(db: Session) -> dict:
    """Is a rollback across migration 0010 safe right now? ``blockers`` explains every reason:

    * publishing must be PAUSED (older code would otherwise start new uploads);
    * NO active send at all - ``uploading`` (a transfer in progress), ``processing`` (waiting for the
      platform: older code would poll it and could call media_publish without the v0.6.1+ protections)
      and running publish jobs - even when their snapshot matches the current video;
    * pending publications whose approved media differs from the clip's current one must be
      cancelled first (``--cancel-mismatched``) or remain blocked.
    """
    from ..models import Job
    from .editorial import control

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
    paused = bool(control(db).get("paused"))
    active = [{"publication_id": p.id, "status": p.status, "platform": p.platform}
              for p in db.scalars(select(Publication).where(Publication.status.in_(ACTIVE_SEND)))]
    running = [j.id for j in db.scalars(select(Job).where(Job.type == "publish", Job.status == "running"))]
    blockers = []
    if not paused:
        blockers.append("a publicação não está pausada: rode `clipper publish-pause on` antes")
    for a in active:
        blockers.append(f"publicação #{a['publication_id']} ({a['platform']}) está em '{a['status']}': "
                        f"espere terminar ou confira o resultado antes do rollback")
    if running:
        blockers.append(f"{len(running)} job(s) de envio em execução agora: espere terminar")
    for r in rows:
        if not r["cancellable"] and r["status"] not in ACTIVE_SEND:
            blockers.append(f"publicação #{r['publication_id']} com mídia divergente em '{r['status']}'")
        elif r["cancellable"]:
            blockers.append(f"publicação #{r['publication_id']} com mídia aprovada diferente da atual: "
                            f"cancele com `--cancel-mismatched`")
    return {"paused": paused, "mismatched": rows, "active_sends": active, "running_jobs": running,
            "blockers": blockers, "safe_to_rollback": not blockers}


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
