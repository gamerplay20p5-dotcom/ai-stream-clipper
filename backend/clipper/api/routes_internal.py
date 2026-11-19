"""Internal API used by workers. Every request is HMAC-signed (see ``security.py``).

Workers never touch the database or another server's filesystem directly: they claim jobs,
post results and exchange media only through these endpoints (and the capture workers'
segment servers).
"""

from __future__ import annotations

import re
import uuid

import logging

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from sqlalchemy import select

from .. import pipeline, pipeline_ai, queue, routing, storage
from ..config import get_settings
from ..db import get_db, utcnow
from ..models import Clip, Job, Worker
from .deps import worker_auth, worker_auth_upload

log = logging.getLogger(__name__)
router = APIRouter(prefix="/internal", tags=["internal"])

MAX_VIDEO_BYTES = 1024 * 1024 * 1024  # 1 GiB
MAX_THUMB_BYTES = 8 * 1024 * 1024


def _nf(exc: Exception):
    raise HTTPException(404, str(exc)) from exc


# --- workers -------------------------------------------------------------------


class RegisterIn(BaseModel):
    host: str = Field("", max_length=128)
    host_label: str = Field("", max_length=64)
    roles: list[str] = Field(default_factory=list)
    capabilities: dict = Field(default_factory=dict)
    base_url: str = Field("", max_length=512)
    version: str = Field("", max_length=32)


@router.post("/workers/register")
def register(body: RegisterIn, worker: str = Depends(worker_auth), db: Session = Depends(get_db)):
    roles = [r for r in body.roles if r in set(queue.JOB_ROLES.values()) | {"monitor"}]
    w = db.get(Worker, worker)
    now = utcnow()
    if w is None:
        w = Worker(id=worker)
        db.add(w)
    w.host, w.host_label, w.roles = body.host, body.host_label, roles
    w.capabilities, w.base_url, w.version = body.capabilities, body.base_url, body.version
    w.status, w.started_at, w.last_heartbeat_at = "online", now, now
    # jobs held by a previous incarnation of this worker are re-queued immediately
    for j in db.query(Job).filter(Job.status == "running", Job.worker_id == worker):
        queue._requeue_or_bury(j, "worker restarted", retryable=True)
        if j.status == "dead":
            pipeline.on_job_dead(db, j)
    db.commit()
    log.info("worker registered", extra={"worker": worker, "roles": roles, "gpu": body.capabilities.get("gpu")})
    return {"ok": True, "roles": roles}


class HeartbeatIn(BaseModel):
    metrics: dict = Field(default_factory=dict)


@router.post("/workers/heartbeat")
def heartbeat(body: HeartbeatIn, worker: str = Depends(worker_auth), db: Session = Depends(get_db)):
    w = db.get(Worker, worker)
    if w is None:
        raise HTTPException(409, "worker not registered")
    w.metrics = body.metrics
    w.last_heartbeat_at = utcnow()
    w.status = "online"
    db.commit()
    return {"ok": True}


# --- jobs ------------------------------------------------------------------------


class ClaimIn(BaseModel):
    types: list[str]
    limit: int = Field(1, ge=1, le=16)


@router.post("/jobs/claim")
def claim(body: ClaimIn, worker: str = Depends(worker_auth), db: Session = Depends(get_db)):
    w = db.get(Worker, worker)
    if w is None:
        raise HTTPException(409, "worker not registered")
    allowed = [t for t in body.types if queue.JOB_ROLES.get(t) in (w.roles or [])]
    accept = None
    if "vision_analyze" in allowed:
        online = list(db.scalars(select(Worker).where(Worker.status == "online")))
        accept = routing.vision_acceptor(w, online, utcnow())
    jobs = queue.claim(db, worker, allowed, has_gpu=bool((w.capabilities or {}).get("gpu")), limit=body.limit,
                       lease_seconds=pipeline.lease_seconds(), global_limits=pipeline.global_limits(db),
                       accept=accept)
    db.commit()
    return [{"id": j.id, "type": j.type, "payload": j.payload, "attempts": j.attempts} for j in jobs]


@router.post("/jobs/{jid}/heartbeat")
def job_heartbeat(jid: int, worker: str = Depends(worker_auth), db: Session = Depends(get_db)):
    ok, cancel = queue.renew(db, jid, worker, pipeline.lease_seconds())
    db.commit()
    return {"ok": ok, "cancel": cancel}


class CompleteIn(BaseModel):
    result: dict = Field(default_factory=dict)


@router.post("/jobs/{jid}/complete")
def job_complete(jid: int, body: CompleteIn, worker: str = Depends(worker_auth), db: Session = Depends(get_db)):
    ok = queue.complete(db, jid, worker, body.result)
    db.commit()
    return {"ok": ok}


class FailIn(BaseModel):
    error: str = Field("", max_length=8000)
    retryable: bool = True


@router.post("/jobs/{jid}/fail")
def job_fail(jid: int, body: FailIn, worker: str = Depends(worker_auth), db: Session = Depends(get_db)):
    status = queue.fail(db, jid, worker, body.error, retryable=body.retryable)
    if status == "dead":
        pipeline.on_job_dead(db, db.get(Job, jid))
    db.commit()
    return {"ok": status is not None, "status": status}


# --- monitor -----------------------------------------------------------------------


# --- evaluation dataset media (Phase 3) -------------------------------------------------


@router.get("/examples/{eid}/media-spec")
def example_media_spec(eid: int, _: str = Depends(worker_auth), db: Session = Depends(get_db)):
    from ..evaluation import media as ds_media

    try:
        return ds_media.media_spec(db, eid)
    except ds_media.NotAvailable as exc:
        raise HTTPException(404, str(exc)) from exc


@router.put("/examples/{eid}/media")
async def example_media_upload(eid: int, request: Request, start: float, duration: float,
                               auth: tuple[str, str] = Depends(worker_auth_upload), db: Session = Depends(get_db)):
    from ..evaluation import media as ds_media

    try:
        dest = ds_media.media_dest(db, eid)
    except ds_media.NotAvailable as exc:
        raise HTTPException(404, str(exc)) from exc
    size = await _receive(request, dest, MAX_VIDEO_BYTES, auth[1])
    ds_media.store_media(db, eid, dest, size, start, duration)
    db.commit()
    return {"ok": True, "bytes": size}


class ExampleFailIn(BaseModel):
    error: str = Field("", max_length=2000)


@router.post("/examples/{eid}/media-failed")
def example_media_failed(eid: int, body: ExampleFailIn, _: str = Depends(worker_auth), db: Session = Depends(get_db)):
    from ..evaluation import media as ds_media

    ds_media.mark_failed(db, eid, body.error)
    db.commit()
    return {"ok": True}


@router.get("/examples/{eid}/source")
def example_clip_source(eid: int, _: str = Depends(worker_auth), db: Session = Depends(get_db)):
    """Clip source stored in the API (examples of clips whose segments are gone)."""
    from fastapi.responses import FileResponse

    from ..evaluation import media as ds_media

    try:
        path = ds_media.clip_source_path(db, eid)
    except ds_media.NotAvailable as exc:
        raise HTTPException(404, str(exc)) from exc
    return FileResponse(path, media_type="video/mp4")


@router.get("/monitor/targets")
def monitor_targets(min_interval: float = 60.0, _: str = Depends(worker_auth), db: Session = Depends(get_db)):
    return pipeline.monitor_targets(db, max(10.0, min_interval))


class ReportIn(BaseModel):
    streamer_id: int
    live: bool
    title: str = Field("", max_length=1000)
    error: str = Field("", max_length=2000)
    note: str = Field("", max_length=500)
    channel_id: str = Field("", pattern=r"^(UC[A-Za-z0-9_-]{22})?$")


@router.post("/monitor/report")
def monitor_report(body: ReportIn, _: str = Depends(worker_auth), db: Session = Depends(get_db)):
    try:
        out = pipeline.report_status(db, body.streamer_id, body.live, body.title, body.error,
                                     note=body.note, channel_id=body.channel_id)
    except pipeline.NotFound as exc:
        _nf(exc)
    db.commit()
    return out


# --- capture -------------------------------------------------------------------------


@router.get("/sessions/{sid}/state")
def session_state(sid: int, _: str = Depends(worker_auth), db: Session = Depends(get_db)):
    try:
        return pipeline.session_state(db, sid)
    except pipeline.NotFound as exc:
        _nf(exc)


class SegmentsIn(BaseModel):
    base_url: str = Field("", max_length=512)
    segments: list[dict]


@router.post("/sessions/{sid}/segments")
def post_segments(sid: int, body: SegmentsIn, worker: str = Depends(worker_auth), db: Session = Depends(get_db)):
    try:
        n = pipeline.register_segments(db, sid, worker, body.base_url, body.segments)
    except pipeline.NotFound as exc:
        _nf(exc)
    db.commit()
    return {"ok": True, "added": n}


class EvictIn(BaseModel):
    idxs: list[int]
    force: bool = False


@router.post("/sessions/{sid}/evict")
def post_evict(sid: int, body: EvictIn, _: str = Depends(worker_auth), db: Session = Depends(get_db)):
    allowed = pipeline.evict_plan(db, sid, body.idxs, body.force)
    db.commit()
    return {"allowed": allowed}


class EndIn(BaseModel):
    reason: str = Field("", max_length=255)


@router.post("/sessions/{sid}/end")
def post_end(sid: int, body: EndIn, _: str = Depends(worker_auth), db: Session = Depends(get_db)):
    try:
        pipeline.end_session(db, sid, body.reason)
    except pipeline.NotFound as exc:
        _nf(exc)
    db.commit()
    return {"ok": True}


# --- audio / scoring -------------------------------------------------------------------


class AnalysisIn(BaseModel):
    segment_idx: int
    features: dict = Field(default_factory=dict)
    words: list[list] = Field(default_factory=list)
    speech_ratio: float = 0.0
    language: str = ""
    skipped: bool = False
    extra: dict = Field(default_factory=dict)  # Phase 2: yamnet categories + pitch
    speech_turns: list[dict] | None = None  # Phase 5 E: [{start, end, text, emb: [[256] x 1 or 3]}]


@router.post("/sessions/{sid}/analysis")
def post_analysis(sid: int, body: AnalysisIn, _: str = Depends(worker_auth), db: Session = Depends(get_db)):
    try:
        pipeline.post_analysis(db, sid, body.segment_idx, body.model_dump())
    except pipeline.NotFound as exc:
        _nf(exc)
    db.commit()
    return {"ok": True}


@router.get("/sessions/{sid}/timeline")
def get_timeline(sid: int, _: str = Depends(worker_auth), db: Session = Depends(get_db)):
    try:
        return pipeline.timeline(db, sid)
    except pipeline.NotFound as exc:
        _nf(exc)


class CandidatesIn(BaseModel):
    from_scored_until: float
    result: dict


@router.post("/sessions/{sid}/candidates")
def post_candidates(sid: int, body: CandidatesIn, _: str = Depends(worker_auth), db: Session = Depends(get_db)):
    try:
        ids = pipeline.post_candidates(db, sid, body.from_scored_until, body.result)
    except pipeline.NotFound as exc:
        _nf(exc)
    except pipeline.Conflict as exc:
        db.rollback()
        raise HTTPException(409, str(exc)) from exc
    db.commit()
    return {"clip_ids": ids}


# --- vision -------------------------------------------------------------------------------


@router.get("/clips/{cid}/vision-spec")
def vision_spec(cid: int, _: str = Depends(worker_auth), db: Session = Depends(get_db)):
    try:
        return pipeline.vision_spec(db, cid)
    except pipeline.NotFound as exc:
        _nf(exc)


class VisionIn(BaseModel):
    result: dict | None = None
    error: str = Field("", max_length=2000)


class AnalysisResultIn(BaseModel):
    decision: dict
    edit_plan: dict
    visual: dict | None = None
    narrative: dict | None = None
    heuristic_vision: dict | None = None
    meta: dict = Field(default_factory=dict)


@router.post("/clips/{cid}/analysis")
def post_ai_analysis(cid: int, body: AnalysisResultIn, worker: str = Depends(worker_auth),
                     db: Session = Depends(get_db)):
    clip = _clip_or_404(db, cid)
    if clip.vision_state != "pending":
        return {"ok": False, "stale": True}  # already decided (timeout fallback or newer run)
    try:
        pipeline_ai.apply_analysis(db, clip, body.model_dump(), worker=worker)
    except ValueError as exc:  # schema validation failed -> continue without the AI result
        db.rollback()
        clip = _clip_or_404(db, cid)
        pipeline_ai.direct_without_vision(db, clip, f"resultado de IA inválido: {exc}"[:200])
    db.commit()
    return {"ok": True}


@router.post("/clips/{cid}/vision")
def post_vision(cid: int, body: VisionIn, _: str = Depends(worker_auth), db: Session = Depends(get_db)):
    try:
        pipeline.post_vision(db, cid, body.result, body.error)
    except pipeline.NotFound as exc:
        _nf(exc)
    db.commit()
    return {"ok": True}


# --- render -----------------------------------------------------------------------------------


@router.get("/clips/{cid}/render-spec")
def render_spec(cid: int, variant: str = "", _: str = Depends(worker_auth), db: Session = Depends(get_db)):
    try:
        return pipeline.render_spec(db, cid, variant[:16])
    except pipeline.NotFound as exc:
        _nf(exc)


def _clip_or_404(db: Session, cid: int) -> Clip:
    c = db.get(Clip, cid)
    if c is None:
        raise HTTPException(404, "clip not found")
    return c


async def _receive(request: Request, dest, max_bytes: int, sha: str) -> int:
    try:
        return await storage.save_stream(request.stream(), dest, max_bytes, sha)
    except storage.StorageFull as exc:
        raise HTTPException(507, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.put("/clips/{cid}/source")
async def put_source(cid: int, request: Request, source_start: float, duration: float,
                     auth: tuple[str, str] = Depends(worker_auth_upload), db: Session = Depends(get_db)):
    _clip_or_404(db, cid)
    dest = storage.clip_dir(cid) / "source.mp4"
    await _receive(request, dest, MAX_VIDEO_BYTES, auth[1])
    pipeline.store_source(db, cid, storage.rel(dest), source_start, duration)
    db.commit()
    return {"ok": True}


@router.get("/clips/{cid}/source")
def get_source(cid: int, _: str = Depends(worker_auth), db: Session = Depends(get_db)):
    c = _clip_or_404(db, cid)
    if not c.source_path:
        raise HTTPException(404, "no stored source")
    p = storage.resolve(c.source_path)
    if not p.is_file():
        raise HTTPException(404, "source file missing")
    from ..security import sha256_file

    return FileResponse(p, media_type="video/mp4", headers={"X-ASC-SHA256": sha256_file(p)})


@router.put("/clips/{cid}/thumbnail")
async def put_thumb(cid: int, request: Request, version: int, variant: str = "", render_id: str = "",
                    auth: tuple[str, str] = Depends(worker_auth_upload), db: Session = Depends(get_db)):
    _clip_or_404(db, cid)
    dest = storage.clip_dir(cid) / _media_name("thumb", version, variant, render_id, "jpg")
    await _receive(request, dest, MAX_THUMB_BYTES, auth[1])
    return {"ok": True}


@router.put("/clips/{cid}/video")
async def put_video(cid: int, request: Request, version: int, seconds: float = 0.0, layout: str = "",
                    variant: str = "", render_id: str = "", auth: tuple[str, str] = Depends(worker_auth_upload),
                    db: Session = Depends(get_db)):
    """The SERVER names every file (unique per render: ``render_id``), so no delivery can ever
    overwrite a file referenced by the main video, a variant or a publication snapshot."""
    from ..pipeline_structure import referenced_files, store_variant

    clip = _clip_or_404(db, cid)
    d = storage.clip_dir(cid)
    rid = render_id if _RID.match(render_id or "") else uuid.uuid4().hex[:12]
    refs = referenced_files(db, clip)
    dest = d / _media_name("clip", version, variant, rid, "mp4")
    if storage.rel(dest) in refs:  # defensive: never write over a referenced file
        rid = uuid.uuid4().hex[:12]
        dest = d / _media_name("clip", version, variant, rid, "mp4")
    size = await _receive(request, dest, MAX_VIDEO_BYTES, auth[1])
    thumb = d / _media_name("thumb", version, variant, render_id, "jpg")
    thumb_rel = storage.rel(thumb) if thumb.exists() else ""
    if variant:  # an alternative edit: stored next to the main video, nothing else changes
        name = _variant_name(variant)
        stored, drop = store_variant(db, clip, name, storage.rel(dest), thumb_rel, size, auth[1], seconds, render_id)
        db.commit()
        _delete_unreferenced(db, clip, drop)
        return {"ok": stored, "variant": name, **({} if stored else {"stale": True})}
    expected = ((clip.provenance or {}).get("render") or {}).get("expected")
    old = [clip.video_path, clip.thumb_path]
    stale = bool(render_id and expected and render_id != expected) or (
        render_id and expected is None and ((clip.provenance or {}).get("render") or {}).get("promoted"))
    stored = (not stale) and pipeline.store_render(db, cid, int(version), storage.rel(dest), thumb_rel, size, auth[1],
                                                   seconds, layout[:16])
    db.commit()
    if not stored:  # older version / other render id: an obsolete render never replaces a newer video
        _delete_unreferenced(db, clip, [storage.rel(dest), thumb_rel])
        return {"ok": False, "stale": True}
    _delete_unreferenced(db, clip, old)
    return {"ok": True}


_RID = re.compile(r"^[0-9a-f]{6,32}$")


def _media_name(kind: str, version: int, variant: str, render_id: str, ext: str) -> str:
    rid = render_id if _RID.match(render_id or "") else ""
    return f"{kind}_v{int(version)}" + (f"_{_variant_name(variant)}" if variant else "") + (f"_{rid}" if rid else "") + f".{ext}"


def _delete_unreferenced(db: Session, clip: Clip, paths: list[str]) -> None:
    from ..pipeline_structure import referenced_files

    db.refresh(clip)
    keep = referenced_files(db, clip)
    for rel_path in paths:
        if rel_path and rel_path not in keep:
            try:
                storage.resolve(rel_path).unlink(missing_ok=True)
            except ValueError:
                pass


def _variant_name(v: str) -> str:
    if v not in ("edit", "original"):
        raise HTTPException(422, "unknown variant")
    return v


def internal_enabled() -> bool:
    return bool(get_settings().internal_secret)
