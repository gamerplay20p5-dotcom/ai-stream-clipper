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
from .models import Clip, ClipShare, Streamer, StreamerGrant, User

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
        or_(ClipShare.expires_at.is_(None), ClipShare.expires_at > now),
        or_(ClipShare.video_path != "", Clip.video_path != ""))
        .order_by(ClipShare.created_at.desc())).all()
    return [(s, c) for s, c in rows]


def snapshot(s: ClipShare, clip: Clip) -> None:
    """Freeze the exact media approved now (main video, thumbnail and the variants ready now)."""
    s.video_path, s.video_sha256, s.video_bytes = clip.video_path, clip.sha256 or "", clip.file_size or 0
    s.thumb_path, s.clip_version = clip.thumb_path or "", clip.version or 0
    s.variants = {name: {"video_path": rec["video_path"], "sha256": rec.get("sha256", ""),
                         "label": rec.get("label") or name}
                  for name, rec in ((clip.provenance or {}).get("variants") or {}).items()
                  if rec.get("state") == "done" and rec.get("video_path")}


def media(s: ClipShare, clip: Clip) -> dict:
    """What this share serves: the frozen snapshot, or the clip's current media with follow_latest."""
    if s.follow_latest or not s.video_path:
        live = {name: {"video_path": rec["video_path"], "label": rec.get("label") or name}
                for name, rec in ((clip.provenance or {}).get("variants") or {}).items()
                if rec.get("state") == "done" and rec.get("video_path")}
        return {"video": clip.video_path, "thumb": clip.thumb_path, "variants": live}
    return {"video": s.video_path, "thumb": s.thumb_path, "variants": s.variants or {}}


def active_shares_of_clip(db: Session, clip_id: int) -> list[ClipShare]:
    now = utcnow()
    return list(db.scalars(select(ClipShare).where(
        ClipShare.clip_id == clip_id, ClipShare.revoked_at.is_(None),
        or_(ClipShare.expires_at.is_(None), ClipShare.expires_at > now))))


def protected_files(db: Session, clip: Clip) -> set[str]:
    """Files that must stay on disk while an authorisation is valid (snapshot or live media)."""
    out: set[str] = set()
    for s in active_shares_of_clip(db, clip.id):
        m = media(s, clip)
        out.update(p for p in (m["video"], m["thumb"]) if p)
        out.update(v["video_path"] for v in m["variants"].values() if v.get("video_path"))
    return out


def share(db: Session, *, admin_id: int, email: str, clip_ids: list[int], can_download: bool = False,
          can_variants: bool = False, expires_at: datetime | None = None, follow_latest: bool = False) -> dict:
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
        s.follow_latest = follow_latest
        snapshot(s, clip)  # re-sharing re-approves the CURRENT version explicitly
        db.flush()
        done.append(s.id)
    return {"user_id": user.id, "email": user.email, "shared": done, "refused": refused}


# ------------------------------------------------------------------ access by streamer
def _user_for(db: Session, email: str) -> User:
    user = db.scalar(select(User).where(User.email == normalize_email(email)))
    if user is None:
        raise ShareError("conta não encontrada: crie a conta antes de dar acesso (nenhum e-mail é enviado)")
    if not user.active:
        raise ShareError("a conta está desativada: reative antes de dar acesso")
    return user


def active_grants(db: Session, *, user_id: int | None = None, streamer_id: int | None = None) -> list[StreamerGrant]:
    now = utcnow()
    q = select(StreamerGrant).where(StreamerGrant.revoked_at.is_(None),
                                    or_(StreamerGrant.expires_at.is_(None), StreamerGrant.expires_at > now))
    if user_id is not None:
        q = q.where(StreamerGrant.user_id == user_id)
    if streamer_id is not None:
        q = q.where(StreamerGrant.streamer_id == streamer_id)
    return list(db.scalars(q.order_by(StreamerGrant.id)))


def grant_statuses(g: StreamerGrant) -> tuple[str, ...]:
    return ("ready", "approved") if g.include_pending else ("approved",)


def _share_from_grant(db: Session, g: StreamerGrant, clip: Clip) -> ClipShare | None:
    """One rendered clip the grant covers -> a share for the grant's account (never duplicates an active share)."""
    if clip.status not in grant_statuses(g) or not clip.video_path or clip.streamer_id != g.streamer_id:
        return None
    if db.scalar(select(ClipShare.id).where(ClipShare.clip_id == clip.id, ClipShare.user_id == g.user_id,
                                            ClipShare.revoked_at.is_(None))):
        return None  # already shared (explicitly or by this grant): keep what the admin chose
    s = ClipShare(clip_id=clip.id, user_id=g.user_id, created_by=g.created_by, can_view=True,
                  can_download=g.can_download, can_variants=False, expires_at=g.expires_at, grant_id=g.id)
    snapshot(s, clip)  # the version approved now, like any share
    db.add(s)
    db.flush()
    return s


def sync_grant(db: Session, g: StreamerGrant) -> int:
    """Share every clip the grant covers (approved, and also pending review if chosen) not shared yet."""
    n = 0
    for clip in db.scalars(select(Clip).where(Clip.streamer_id == g.streamer_id, Clip.status.in_(grant_statuses(g)),
                                              Clip.is_test.is_(False), Clip.video_path != "")):
        if _share_from_grant(db, g, clip) is not None:
            n += 1
    return n


def sync_all_grants(db: Session) -> int:
    """Safety net (maintenance tick): approvals made by any path end up shared."""
    return sum(sync_grant(db, g) for g in active_grants(db))


def on_clip_status(db: Session, clip: Clip) -> None:
    """Called after a clip is approved or rejected: grants follow the human decision.
    Approved -> shared with every account that has access to the streamer.
    Ready for review -> shared with accounts whose access includes pending clips (nothing is revoked:
    a re-render of an approved clip also passes through "ready").
    Rejected or failed -> shares created BY A GRANT are revoked (explicit shares are never touched)."""
    if clip.is_test:
        return
    if clip.status in ("approved", "ready"):
        for g in active_grants(db, streamer_id=clip.streamer_id):
            _share_from_grant(db, g, clip)
        return
    now = utcnow()
    for s in db.scalars(select(ClipShare).where(ClipShare.clip_id == clip.id, ClipShare.grant_id.is_not(None),
                                                ClipShare.revoked_at.is_(None))):
        s.revoked_at = now


def grant_streamer(db: Session, *, admin_id: int, email: str, streamer_id: int, can_download: bool = False,
                   expires_at: datetime | None = None, include_pending: bool = False) -> tuple[StreamerGrant, int]:
    user = _user_for(db, email)
    if user.role == "admin":
        raise ShareError("contas da equipe já veem todos os cortes")
    if db.get(Streamer, streamer_id) is None:
        raise ShareError("streamer não encontrado")
    if expires_at is not None and expires_at <= utcnow():
        raise ShareError("a data de expiração já passou")
    g = db.scalar(select(StreamerGrant).where(StreamerGrant.user_id == user.id, StreamerGrant.streamer_id == streamer_id,
                                              StreamerGrant.revoked_at.is_(None)))
    if g is None:
        g = StreamerGrant(user_id=user.id, streamer_id=streamer_id, created_by=admin_id)
        db.add(g)
    g.can_download, g.expires_at, g.include_pending = can_download, expires_at, include_pending
    db.flush()
    now = utcnow()
    for s in db.scalars(select(ClipShare).where(ClipShare.grant_id == g.id, ClipShare.revoked_at.is_(None))):
        s.can_download, s.expires_at = can_download, expires_at  # changing the grant updates its shares
        c = db.get(Clip, s.clip_id)
        if c is not None and c.status not in grant_statuses(g):
            s.revoked_at = now  # pending clips leave when the access goes back to "approved only"
    return g, sync_grant(db, g)


def revoke_grant(db: Session, grant_id: int, admin_id: int) -> tuple[StreamerGrant, int]:
    g = db.get(StreamerGrant, grant_id)
    if g is None:
        raise ShareError("acesso por streamer não encontrado")
    now = utcnow()
    n = 0
    if g.revoked_at is None:
        g.revoked_at, g.revoked_by = now, admin_id
    for s in db.scalars(select(ClipShare).where(ClipShare.grant_id == g.id, ClipShare.revoked_at.is_(None))):
        s.revoked_at, s.revoked_by = now, admin_id
        n += 1
    return g, n


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
        for name, rec in media(s, c)["variants"].items():
            variants.append({"name": name, "label": rec.get("label") or name,
                             "video_url": f"/api/library/{c.id}/variants/{name}/video"})
    return {
        "clip_id": c.id, "title": c.title or f"Corte #{c.id}",
        "streamer": (st.display_name or st.channel) if st else "",
        "duration_s": round(max(0.0, c.end - c.start), 1),
        "shared_at": s.created_at.isoformat() + "Z" if s.created_at else None,
        "expires_at": s.expires_at.isoformat() + "Z" if s.expires_at else None,
        "permissions": {"view": True, "download": s.can_download, "variants": s.can_variants},
        "approved": c.status == "approved",  # pending review clips are labelled in the library
        "version": "atual" if s.follow_latest else "aprovada no compartilhamento",
        "video_url": f"/api/library/{c.id}/video", "thumbnail_url": f"/api/library/{c.id}/thumbnail",
        "download_url": f"/api/library/{c.id}/download" if s.can_download else None,
        "variants": variants,
    }
