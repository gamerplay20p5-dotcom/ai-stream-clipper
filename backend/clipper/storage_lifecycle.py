"""Phase 5 stage C: score-based storage lifecycle for clip media (quarantine -> purge).

Safety model
* Only MEDIA files inside the clip's own storage folder are ever deleted (rendered video, source
  extract, thumbnails). Database rows, labels, reviews, publications and audit stay forever.
* Two steps: quarantine (recoverable, ``purge_after``) -> purge after the quarantine period.
* Protected and never touched: approved, favourite, manually protected, published/scheduled,
  anything with work in progress, sources still needed by a dataset job, test clips with labels.
* Default: disabled + dry run. Clips that existed before the cleanup was enabled are excluded
  unless the owner explicitly authorises the retroactive cleanup.
* Idempotent: every action is checked again under a row lock right before it happens and logged
  in ``cleanup_log`` (dry runs included).
"""

from __future__ import annotations

import logging
import shutil
from datetime import timedelta
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from . import storage
from .db import utcnow
from .models import AppSetting, CleanupLog, Clip, EvalExample, Job, Publication, Review

log = logging.getLogger(__name__)

STATE_KEY = "cleanup_state"
BUSY = ("candidate", "analyzing", "rendering", "waiting")


# ------------------------------------------------------------------ state (activation time / retro authorisation)
def state(db: Session) -> dict:
    row = db.get(AppSetting, STATE_KEY)
    return dict(row.value or {}) if row is not None else {}


def save_state(db: Session, value: dict) -> None:
    row = db.get(AppSetting, STATE_KEY)
    if row is None:
        row = AppSetting(key=STATE_KEY, value={})
        db.add(row)
    row.value = dict(value)
    db.flush()


# ------------------------------------------------------------------ artefacts on disk
def artefacts(clip: Clip) -> dict[str, list[Path]]:
    """Files of this clip by kind. Only inside storage/clips/<id>/ (never anything else)."""
    d = storage.root() / "clips" / str(int(clip.id))
    out: dict[str, list[Path]] = {"video": [], "source": [], "thumbnail": []}
    if not d.is_dir():
        return out
    for p in d.iterdir():
        if not p.is_file():
            continue
        if p.name.startswith("video") and p.suffix == ".mp4":
            out["video"].append(p)
        elif p.name.startswith("source"):
            out["source"].append(p)
        elif p.name.startswith("thumb"):
            out["thumbnail"].append(p)
    return out


def _size(paths: list[Path]) -> int:
    total = 0
    for p in paths:
        try:
            total += p.stat().st_size
        except OSError:
            pass
    return total


# ------------------------------------------------------------------ classification
def protection(db: Session, clip: Clip) -> list[str]:
    """Reasons why this clip's media must not be removed (empty = not protected)."""
    why = []
    if clip.status == "approved":
        why.append("aprovado por você")
    if clip.favorite:
        why.append("favorito")
    if clip.protected:
        why.append("protegido manualmente")
    if clip.status in BUSY:
        why.append(f"em processamento ({clip.status})")
    pubs = db.scalars(select(Publication.status).where(Publication.clip_id == clip.id)).all()
    if any(s in ("scheduled", "uploading", "processing", "published", "draft") for s in pubs):
        why.append("publicado ou agendado")
    running = any(int((j.payload or {}).get("clip_id", -1)) == clip.id for j in db.scalars(
        select(Job).where(Job.status.in_(("queued", "running")), Job.type.in_(("render", "vision_analyze")))))
    if running:
        why.append("job em execução usando a mídia")
    ex = db.scalar(select(EvalExample).where(EvalExample.clip_id == clip.id))
    if ex is not None and ex.media_state == "pending":
        why.append("mídia ainda sendo copiada para o dataset de avaliação")
    if clip.is_test and ex is not None and db.scalar(select(Review.id).where(Review.example_id == ex.id)):
        why.append("corte de teste com avaliação")
    return why


def eligibility(clip: Clip, settings, now) -> tuple[bool, str, int]:
    """(eligible, reason, days to wait). Uses the editorial score when it exists, else the score."""
    score = clip.editorial_score if clip.editorial_score is not None else clip.score
    age = now - (clip.updated_at or clip.created_at)
    if clip.status == "rejected":
        days = settings.cleanup_rejected_days
        return age >= timedelta(days=days), f"rejeitado (nota {score:.0f})", days
    if clip.status == "failed":
        return age >= timedelta(days=settings.cleanup_rejected_days), "renderização falhou", settings.cleanup_rejected_days
    if clip.status == "ready" and score < settings.cleanup_keep_min_score:
        days = settings.cleanup_below_min_days
        return age >= timedelta(days=days), f"nota {score:.0f} abaixo do mínimo para manter " \
                                            f"({settings.cleanup_keep_min_score:.0f})", days
    return False, "", 0


def plan(db: Session, settings, *, include_existing: bool | None = None, limit: int = 2000) -> dict:
    """What the cleanup WOULD do now (never changes anything)."""
    now = utcnow()
    st = state(db)
    since = st.get("enabled_at")
    retro = st.get("retro_authorized") if include_existing is None else include_existing
    out = {"eligible": [], "protected": [], "waiting_period": [], "excluded_existing": 0,
           "bytes": {"video": 0, "source": 0, "thumbnail": 0}, "files": 0}
    for clip in db.scalars(select(Clip).where(Clip.lifecycle == "active").order_by(Clip.id).limit(limit)):
        ok, reason, days = eligibility(clip, settings, now)
        if not reason:
            continue
        guard = protection(db, clip)
        files = artefacts(clip)
        sel = {k: v for k, v in files.items() if getattr(settings, f"cleanup_purge_{k}", True)}
        size = {k: _size(v) for k, v in sel.items()}
        item = {"clip_id": clip.id, "status": clip.status, "title": clip.title[:80], "reason": reason,
                "score": round(clip.editorial_score if clip.editorial_score is not None else clip.score, 1),
                "bytes": sum(size.values()), "files": sum(len(v) for v in sel.values()),
                "created_at": clip.created_at.isoformat() + "Z"}
        if guard:
            out["protected"].append({**item, "protected_by": guard})
            continue
        if not retro and (since is None or clip.created_at.isoformat() < since):
            out["excluded_existing"] += 1
            item["note"] = "já existia antes da limpeza: só com autorização retroativa"
            out["protected"].append({**item, "protected_by": ["anterior à ativação (sem autorização retroativa)"]})
            continue
        if not ok:
            out["waiting_period"].append({**item, "days": days})
            continue
        out["eligible"].append(item)
        for k, v in size.items():
            out["bytes"][k] += v
        out["files"] += item["files"]
    out["total_bytes"] = sum(out["bytes"].values())
    out["counts"] = {k: len(out[k]) for k in ("eligible", "protected", "waiting_period")}
    return out


# ------------------------------------------------------------------ actions
def _log(db: Session, clip_id: int | None, action: str, reason: str, freed: int = 0, dry: bool = False,
         actor: str = "system") -> None:
    db.add(CleanupLog(clip_id=clip_id, action=action, reason=reason[:255], bytes_freed=int(freed), dry_run=dry,
                      actor=actor))


def _locked(db: Session, clip_id: int) -> Clip | None:
    q = select(Clip).where(Clip.id == clip_id)
    if db.get_bind().dialect.name == "postgresql":
        q = q.with_for_update()
    return db.scalar(q)


def quarantine(db: Session, clip_id: int, reason: str, settings, actor: str = "system") -> bool:
    clip = _locked(db, clip_id)
    if clip is None or clip.lifecycle != "active" or protection(db, clip):
        return False
    clip.lifecycle = "quarantined"
    clip.quarantined_at = utcnow()
    clip.purge_after = utcnow() + timedelta(days=settings.cleanup_quarantine_days)
    clip.purge_reason = reason[:255]
    _log(db, clip.id, "quarantine", reason, actor=actor)
    return True


def restore(db: Session, clip_id: int, actor: str) -> bool:
    clip = _locked(db, clip_id)
    if clip is None or clip.lifecycle != "quarantined":
        return False
    clip.lifecycle, clip.quarantined_at, clip.purge_after, clip.purge_reason = "active", None, None, ""
    _log(db, clip.id, "restore", "restaurado antes da exclusão definitiva", actor=actor)
    return True


def purge(db: Session, clip_id: int, settings, actor: str = "system") -> int:
    """Delete the quarantined clip's selected media files. Re-checks protection; idempotent."""
    clip = _locked(db, clip_id)
    if clip is None or clip.lifecycle != "quarantined" or clip.purge_after is None or clip.purge_after > utcnow():
        return 0
    guard = protection(db, clip)
    if guard:  # became protected meanwhile (approved, published...): back to active, nothing deleted
        clip.lifecycle, clip.purge_after = "active", None
        _log(db, clip.id, "purge_cancelled", "; ".join(guard), actor=actor)
        return 0
    freed = 0
    root = (storage.root() / "clips" / str(int(clip.id))).resolve()
    for kind, paths in artefacts(clip).items():
        if not getattr(settings, f"cleanup_purge_{kind}", True):
            continue
        for p in paths:
            rp = p.resolve()
            if root not in rp.parents:  # never outside the clip's own folder
                continue
            try:
                size = rp.stat().st_size
                rp.unlink()
                freed += size
                _log(db, clip.id, f"purge_{kind}", rp.name, size, actor=actor)
            except FileNotFoundError:
                continue  # already gone (interrupted earlier): idempotent
    if settings.cleanup_purge_video:
        clip.video_path, clip.file_size = "", 0
    if settings.cleanup_purge_thumbnail:
        clip.thumb_path = ""
    if settings.cleanup_purge_source:
        clip.source_path = ""
    clip.lifecycle, clip.media_purged_at = "purged", utcnow()
    return freed


def run(db: Session, settings) -> dict:
    """Maintenance step. Disabled or dry-run -> only logs the simulation summary."""
    out = {"quarantined": 0, "purged": 0, "freed": 0, "dry_run": True}
    if not settings.cleanup_enabled:
        return out
    st = state(db)
    if not st.get("enabled_at"):
        st["enabled_at"] = utcnow().isoformat()
        save_state(db, st)
    p = plan(db, settings)
    if settings.cleanup_dry_run:
        _log(db, None, "simulation", f"{p['counts']['eligible']} cortes, {p['total_bytes']} bytes", dry=True)
        return out
    out["dry_run"] = False
    # disk pressure: lowest scores first; otherwise oldest first
    used = sum(_size([f for fl in artefacts(c).values() for f in fl])
               for c in db.scalars(select(Clip).where(Clip.lifecycle == "active")))
    over = used > settings.cleanup_disposable_quota_gb * 1024**3
    items = sorted(p["eligible"], key=lambda i: (i["score"], i["created_at"])) if over else p["eligible"]
    for it in items:
        if quarantine(db, it["clip_id"], it["reason"], settings):
            out["quarantined"] += 1
    for clip in db.scalars(select(Clip).where(Clip.lifecycle == "quarantined", Clip.purge_after <= utcnow())):
        freed = purge(db, clip.id, settings)
        if freed or clip.lifecycle == "purged":
            out["purged"] += 1
            out["freed"] += freed
    return out


def overview(db: Session, settings) -> dict:
    by_kind = {"video": 0, "source": 0, "thumbnail": 0}
    quarantine_bytes = 0
    counts = {"active": 0, "quarantined": 0, "purged": 0}
    for clip in db.scalars(select(Clip)):
        counts[clip.lifecycle] = counts.get(clip.lifecycle, 0) + 1
        files = artefacts(clip)
        for k, v in files.items():
            sz = _size(v)
            by_kind[k] += sz
            if clip.lifecycle == "quarantined":
                quarantine_bytes += sz
    dataset_bytes = int(db.scalar(select(func.coalesce(func.sum(EvalExample.media_bytes), 0))) or 0)
    recovered = int(db.scalar(select(func.coalesce(func.sum(CleanupLog.bytes_freed), 0)).where(
        CleanupLog.dry_run.is_(False))) or 0)
    try:
        du = shutil.disk_usage(storage.root())
        disk = {"total": du.total, "free": du.free}
    except OSError:
        disk = {}
    st = state(db)
    return {"bytes": {**by_kind, "dataset": dataset_bytes, "quarantine": quarantine_bytes}, "clips": counts,
            "recovered_bytes": recovered, "disk": disk, "enabled": settings.cleanup_enabled,
            "dry_run": settings.cleanup_dry_run, "enabled_at": st.get("enabled_at"),
            "retro_authorized": bool(st.get("retro_authorized")),
            "quota_bytes": int(settings.cleanup_disposable_quota_gb * 1024**3)}
