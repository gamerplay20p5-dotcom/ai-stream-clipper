"""Dashboard stats, AI settings, infrastructure, jobs and audit endpoints."""

from __future__ import annotations

from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .. import queue, storage
from ..db import get_db, utcnow
from ..models import AuditLog, Clip, Job, Segment, Streamer, StreamSession, Worker
from ..settings_store import AISettings, load_ai_settings, save_ai_settings
from . import serializers as ser
from .deps import CurrentUser, audit, current_user, require_admin

router = APIRouter(prefix="/api", tags=["admin"])

KNOWN_HOSTS = {
    "201": {"hostname": "1htsserver", "ip": "192.168.1.201", "role": "Worker IA secundário (visão, GPU Intel Arc A750)",
            "gpu": "Intel Arc A750"},
    "206": {"hostname": "website-applications", "ip": "192.168.1.206",
            "role": "Monitoramento, captura, áudio/transcrição, renderização", "gpu": None},
    "210": {"hostname": "2htswebserver", "ip": "192.168.1.210",
            "role": "API, dashboard, orquestração, IA principal (GPU Intel Arc A750)", "gpu": "Intel Arc A750"},
}


@router.get("/stats")
def stats(db: Session = Depends(get_db), _: CurrentUser = Depends(current_user)):
    real = Clip.is_test.is_(False)  # TESTE MANUAL clips never count in quality metrics
    clip_counts = dict(db.execute(select(Clip.status, func.count()).where(real).group_by(Clip.status)).all())
    seg_seconds = db.scalar(select(func.coalesce(func.sum(Segment.duration), 0.0))
                            .where(Segment.analysis_state == "done")) or 0.0
    since = utcnow() - timedelta(hours=24)
    recent = db.execute(
        select(Clip.created_at).where(Clip.created_at >= since, real)
    ).all()
    buckets = [0] * 24
    now = utcnow()
    for (ts,) in recent:
        h = int((now - ts).total_seconds() // 3600)
        if 0 <= h < 24:
            buckets[23 - h] += 1
    pending = db.scalar(select(func.count()).select_from(Job).where(Job.status.in_(("queued", "running")),
                                                                    Job.type != "capture")) or 0
    workers = db.scalars(select(Worker)).all()
    top = db.scalars(select(Clip).where(Clip.status.in_(("ready", "approved")), real)
                     .order_by(Clip.score.desc()).limit(5)).all()
    streamers = {s.id: s for s in db.scalars(select(Streamer))}
    return {
        "streamers_total": len(streamers),
        "streamers_enabled": sum(1 for s in streamers.values() if s.enabled),
        "live_sessions": db.scalar(select(func.count()).select_from(StreamSession)
                                   .where(StreamSession.status == "live")) or 0,
        "hours_analyzed": round(seg_seconds / 3600.0, 2),
        "clips_total": sum(clip_counts.values()),
        "clips_by_status": clip_counts,
        "clips_approved": clip_counts.get("approved", 0),
        "clips_last_24h": buckets,
        "pending_jobs": pending,
        "dead_jobs": db.scalar(select(func.count()).select_from(Job).where(Job.status == "dead")) or 0,
        "workers_online": sum(1 for w in workers if w.status == "online"),
        "workers_total": len(workers),
        "top_clips": [ser.clip(c, streamers.get(c.streamer_id)) for c in top],
    }


@router.get("/patch-notes")
def patch_notes(_: CurrentUser = Depends(current_user)):
    from .. import __version__
    from ..patch_notes import NOTES

    return {"current": __version__, "notes": NOTES}


@router.get("/settings")
def get_ai_settings(db: Session = Depends(get_db), _: CurrentUser = Depends(current_user)):
    return load_ai_settings(db).model_dump()


@router.put("/settings")
def put_ai_settings(body: dict, request: Request, db: Session = Depends(get_db),
                    user: CurrentUser = Depends(require_admin)):
    current = load_ai_settings(db).model_dump()
    merged = {**current, **body}
    if "weights" in body and isinstance(body["weights"], dict):
        merged["weights"] = {**current["weights"], **body["weights"]}
    try:
        s = AISettings.model_validate(merged)
    except ValidationError as exc:
        raise HTTPException(422, exc.errors(include_url=False, include_context=False)) from exc
    save_ai_settings(db, s)
    changed = {k: merged[k] for k in body if k in merged and current.get(k) != merged[k]}
    audit(db, request, user, "settings.update", "ai", changed)
    db.commit()
    return s.model_dump()


@router.get("/infrastructure")
def infrastructure(db: Session = Depends(get_db), _: CurrentUser = Depends(current_user)):
    workers = db.scalars(select(Worker).order_by(Worker.host_label, Worker.id)).all()
    hosts: dict[str, dict] = {k: {**v, "label": k, "workers": [], "metrics": None} for k, v in KNOWN_HOSTS.items()}
    for w in workers:
        label = w.host_label or w.host or "?"
        h = hosts.setdefault(label, {"label": label, "hostname": w.host, "ip": "", "role": "", "gpu": None,
                                     "workers": [], "metrics": None})
        h["workers"].append(ser.worker(w))
        if w.status == "online" and w.metrics and (h["metrics"] is None or w.last_heartbeat_at.isoformat() >
                                                   (h["metrics"].get("_at") or "")):
            h["metrics"] = {**w.metrics, "_at": w.last_heartbeat_at.isoformat()}
    # processing time per job type (last 24h, done jobs)
    since = utcnow() - timedelta(hours=24)
    done = db.execute(select(Job.type, Job.started_at, Job.finished_at)
                      .where(Job.status == "done", Job.finished_at >= since, Job.type != "capture")).all()
    timing: dict[str, list[float]] = {}
    for t, a, b in done:
        if a and b:
            timing.setdefault(t, []).append((b - a).total_seconds())
    avg = {t: {"avg_s": round(sum(v) / len(v), 2), "count": len(v), "max_s": round(max(v), 2)}
           for t, v in timing.items()}
    failures = db.scalars(select(Job).where(Job.status == "dead").order_by(Job.id.desc()).limit(20)).all()
    return {
        "hosts": list(hosts.values()),
        "queues": queue.queue_stats(db),
        "processing": avg,
        "failures": [ser.job(j) for j in failures],
        "storage": storage.disk_info(),
    }


@router.get("/ai/models")
def ai_models(db: Session = Depends(get_db), _: CurrentUser = Depends(current_user)):
    """Model catalogue + where each model is installed/loaded and how it performs."""
    from ..ai.registry import MODELS

    workers = [w for w in db.scalars(select(Worker)) if {"vision", "audio"} & set(w.roles or [])]
    out = []
    for key, spec in MODELS.items():
        hosts = []
        for w in workers:
            caps, metrics = w.capabilities or {}, w.metrics or {}
            st = ((metrics.get("ai") or {}).get("models") or {}).get(key)
            if key in (caps.get("ai_models") or []) or key in (caps.get("acoustic_models") or []) or st:
                hosts.append({"worker": w.id, "host_label": w.host_label, "online": w.status == "online",
                              "gpu": bool(caps.get("gpu")), "gpu_name": caps.get("gpu_name", ""),
                              "vram_total_gb": caps.get("vram_total_gb"),
                              "loaded": (metrics.get("ai") or {}).get("loaded") == key, "stats": st})
        out.append({"key": key, "kind": spec.kind, "family": spec.family, "size_gb": spec.approx_size_gb,
                    "min_vram_gb": spec.min_vram_gb, "hf_repo": spec.hf_repo, "notes": spec.notes, "workers": hosts})
    return out


@router.get("/diagnostics")
def diagnostics(db: Session = Depends(get_db), _: CurrentUser = Depends(current_user)):
    from .. import diagnostics as diag

    return diag.report(db)


@router.post("/diagnostics/streamers/{sid}/retry-failed")
def retry_failed(sid: int, request: Request, db: Session = Depends(get_db),
                 user: CurrentUser = Depends(require_admin)):
    from .. import diagnostics as diag

    ids = diag.retry_failed(db, sid)
    audit(db, request, user, "diagnostics.retry_failed", f"streamer:{sid}", {"jobs": ids})
    db.commit()
    return {"retried": ids}


@router.get("/jobs")
def list_jobs(status: str | None = None, type: str | None = None, limit: int = 100,
              db: Session = Depends(get_db), _: CurrentUser = Depends(current_user)):
    q = select(Job).order_by(Job.id.desc()).limit(min(500, max(1, limit)))
    if status:
        q = q.where(Job.status == status)
    if type:
        q = q.where(Job.type == type)
    return [ser.job(j) for j in db.scalars(q)]


@router.post("/jobs/{jid}/retry")
def retry_job(jid: int, request: Request, db: Session = Depends(get_db), user: CurrentUser = Depends(require_admin)):
    j = queue.retry(db, jid)
    if j is None:
        raise HTTPException(409, "job cannot be retried")
    audit(db, request, user, "job.retry", f"job:{jid}")
    db.commit()
    return ser.job(j)


@router.get("/audit")
def audit_log(limit: int = 100, db: Session = Depends(get_db), _: CurrentUser = Depends(require_admin)):
    rows = db.scalars(select(AuditLog).order_by(AuditLog.id.desc()).limit(min(500, max(1, limit))))
    return [{"id": r.id, "user": r.user_email, "action": r.action, "target": r.target, "details": r.details,
             "ip": r.ip, "at": ser.iso(r.created_at)} for r in rows]
