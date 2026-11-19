"""Clip sharing with specific accounts (multi-user stage 1).

Rules (all enforced here, on every request - never trusted from the client):
* a share belongs to (clip, user); the recipient is chosen by the admin by e-mail, stored by user id;
* a share is ACTIVE when not revoked, not expired, the account is active, and the clip still has a
  rendered video; anything else behaves as "not shared" (404, no hint that the clip exists);
* view is the minimum; download and variants are separate, off by default;
* nothing here sends e-mail or creates accounts.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from .db import utcnow
from .models import Clip, ClipShare, Streamer, User

SHAREABLE = ("ready", "approved")


class ShareError(ValueError):
    pass


def normalize_email(email: str) -> str:
    return (email or "").strip().lower()


def active_share(db: Session, user_id: int, clip_id: int) -> ClipShare | None:
    now = utcnow()
    return db.scalar(select(ClipShare).where(
        ClipShare.user_id == user_id, ClipShare.clip_id == clip_id, ClipShare.revoked_at.is_(None),
        ClipShare.can_view.is_(True), or_(ClipShare.expires_at.is_(None), ClipShare.expires_at > now)))


def shared_clips(db: Session, user_id: int) -> list[tuple[ClipShare, Clip]]:
    now = utcnow()
    rows = db.execute(select(ClipShare, Clip).join(Clip, Clip.id == ClipShare.clip_id).where(
        ClipShare.user_id == user_id, ClipShare.revoked_at.is_(None), ClipShare.can_view.is_(True),
        or_(ClipShare.expires_at.is_(None), ClipShare.expires_at > now), Clip.video_path != "")
        .order_by(ClipShare.created_at.desc())).all()
    return [(s, c) for s, c in rows]


def share(db: Session, *, admin_id: int, email: str, clip_ids: list[int], can_download: bool = False,
          can_variants: bool = False, expires_at: datetime | None = None) -> dict:
    """Share clips with an EXISTING active account. Re-sharing an already shared clip updates its
    permissions (one active share per clip+user). Returns what was shared and what was refused."""
    user = db.scalar(select(User).where(User.email == normalize_email(email)))
    if user is None:
        raise ShareError("conta não encontrada: crie a conta antes de compartilhar (nenhum e-mail é enviado)")
    if not user.active:
        raise ShareError("a conta está desativada")
    if expires_at is not None and expires_at <= utcnow():
        raise ShareError("a data de expiração já passou")
    done, refused = [], {}
    for cid in dict.fromkeys(clip_ids):
        clip = db.get(Clip, cid)
        if clip is None:
            refused[cid] = "corte não encontrado"
            continue
        if not clip.video_path or clip.status not in SHAREABLE:
            refused[cid] = f"corte ainda não renderizado ou indisponível ({clip.status})"
            continue
        s = db.scalar(select(ClipShare).where(ClipShare.clip_id == cid, ClipShare.user_id == user.id,
                                              ClipShare.revoked_at.is_(None)))
        if s is None:
            s = ClipShare(clip_id=cid, user_id=user.id, created_by=admin_id)
            db.add(s)
        s.can_view, s.can_download, s.can_variants, s.expires_at = True, can_download, can_variants, expires_at
        db.flush()
        done.append(s.id)
    return {"user_id": user.id, "email": user.email, "shared": done, "refused": refused}


def revoke(db: Session, share_id: int, admin_id: int) -> ClipShare:
    s = db.get(ClipShare, share_id)
    if s is None:
        raise ShareError("compartilhamento não encontrado")
    if s.revoked_at is None:
        s.revoked_at, s.revoked_by = utcnow(), admin_id
    return s


def public_clip(db: Session, s: ClipShare, c: Clip) -> dict:
    """What an external account may see: no file paths, transcripts, scores, analysis or sources."""
    st = db.get(Streamer, c.streamer_id)
    variants = []
    if s.can_variants:
        for name, rec in ((c.provenance or {}).get("variants") or {}).items():
            if rec.get("state") == "done" and rec.get("video_path"):
                variants.append({"name": name, "label": rec.get("label") or name,
                                 "video_url": f"/api/library/{c.id}/variants/{name}/video"})
    return {
        "clip_id": c.id, "title": c.title or f"Corte #{c.id}",
        "streamer": (st.display_name or st.channel) if st else "",
        "duration_s": round(max(0.0, c.end - c.start), 1),
        "shared_at": s.created_at.isoformat() + "Z" if s.created_at else None,
        "expires_at": s.expires_at.isoformat() + "Z" if s.expires_at else None,
        "permissions": {"view": True, "download": s.can_download, "variants": s.can_variants},
        "video_url": f"/api/library/{c.id}/video", "thumbnail_url": f"/api/library/{c.id}/thumbnail",
        "download_url": f"/api/library/{c.id}/download" if s.can_download else None,
        "variants": variants,
    }
