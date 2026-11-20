"""Format profiles in the pipeline (Phase 6C): analysis jobs and the "format" preview variant.

The preview reuses the 6B variant system (same renderer, same render-id/stale protections, shadow
mode: the main video never changes unless a person promotes it).
"""

from __future__ import annotations

from datetime import timedelta
from typing import Literal

from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from . import queue
from .db import utcnow
from .models import Clip, FormatProfile, Job

ANALYZE_PRIORITY = 90  # after live analysis (60), renders (70), variants (80) and deep reviews (85)


class Webcam(BaseModel):
    position: Literal["top", "bottom"] = "top"
    height_pct: int = Field(37, ge=20, le=60)


class Captions(BaseModel):
    position: Literal["low", "middle", "high"] = "low"
    size_pct: int = Field(100, ge=60, le=160)
    style: Literal["bold", "clean", "boxed", "none"] = "bold"
    uppercase: bool = True
    active_color: str = Field("", pattern=r"^(#[0-9a-fA-F]{6})?$")
    emphasis_color: str = Field("", pattern=r"^(#[0-9a-fA-F]{6})?$")


class Badge(BaseModel):
    enabled: bool = False
    position: Literal["top_left", "top_right", "bottom_left", "bottom_right"] = "top_left"
    shape: Literal["circle"] = "circle"
    name: str = Field("", max_length=32)  # OUR name (typed by the owner), never read from the print


class FormatParams(BaseModel):
    """What the renderer can apply. Extra analysis fields (palette, sources...) are kept alongside."""
    layout: Literal["gameplay", "split", "action"] = "gameplay"
    webcam: Webcam = Field(default_factory=Webcam)
    captions: Captions = Field(default_factory=Captions)
    badge: Badge = Field(default_factory=Badge)


def clean_params(raw: dict) -> dict:
    """Validated renderer parameters + the informative extras (palette, contrast, sources...)."""
    base = FormatParams.model_validate({k: raw.get(k) for k in ("layout", "webcam", "captions", "badge")
                                        if raw.get(k) is not None}).model_dump()
    extras = {k: raw[k] for k in ("palette", "contrast", "sources", "confidence", "notes", "version") if k in raw}
    return {**base, **extras}


def budget_ok(db: Session, per_hour: int) -> bool:
    since = utcnow() - timedelta(hours=1)
    return db.query(Job).filter(Job.type == "format_analyze", Job.created_at >= since).count() < per_hour


def enqueue_analysis(db: Session, prof: FormatProfile, settings) -> Job:
    model = settings.vision_model if settings.vision_provider == "openvino_vlm" else None
    prof.status, prof.error = "queued", ""
    return queue.enqueue(db, "format_analyze", {"profile_id": prof.id, "model": model},
                         idempotency_key=f"format:{prof.id}:{utcnow().isoformat()}", priority=ANALYZE_PRIORITY,
                         max_attempts=2)


def store_analysis(db: Session, prof: FormatProfile, result: dict | None, error: str = "") -> None:
    if result is None:
        prof.status, prof.error = "failed", (error or "análise falhou")[:500]
        return
    prof.analysis = {k: result.get(k) for k in ("measured", "vlm", "vlm_error", "version")}
    prof.params = clean_params(result.get("params") or {})
    if prof.params.get("badge", {}).get("enabled") and not prof.params["badge"].get("name"):
        prof.params["badge"]["name"] = ""  # the owner types OUR name; nothing is read from the print
    prof.status, prof.error = "ready", ""


# ------------------------------------------------------------------ preview variant on one of OUR clips
def plan_format_variant(db: Session, clip: Clip, prof: FormatProfile) -> dict:
    from .pipeline_structure import _save_variants, _variants, enqueue_variant

    if clip.status in ("rendering", "analyzing", "candidate", "waiting") or not clip.video_path:
        raise ValueError("o corte precisa estar renderizado")
    if prof.status != "ready" and not prof.params:
        raise ValueError("o perfil de formato ainda não tem parâmetros")
    params = clean_params(prof.params)
    if params["badge"]["enabled"] and not prof.avatar_path:
        params["badge"]["enabled"] = False  # without OUR avatar the badge is skipped (never a placeholder)
    ep = clip.edit_plan or {}
    segs = ep.get("segments") or [{"a": clip.start, "b": clip.end, "role": "main"}]
    v = _variants(clip)
    v["format"] = {"structure": ep.get("structure") or "O", "label": f"Formato: {prof.name}"[:80],
                   "reason": "prévia do perfil de formato no vídeo atual (mesma montagem, só a aparência muda)",
                   "plan": {"segments": segs}, "format_profile_id": prof.id, "format_params": params,
                   "round": int((v.get("format") or {}).get("round", 0)), "manual": True}
    _save_variants(clip, v)
    enqueue_variant(db, clip, "format")
    return v["format"]


def apply_format(spec: dict, rec: dict, clip_id: int) -> dict:
    """Render-spec overrides of a "format" variant (layout, webcam band, captions, badge)."""
    p = rec.get("format_params") or {}
    if not p:
        return spec
    plan = dict(spec.get("edit_plan") or {})
    layout = p.get("layout")
    if layout == "split" and not (plan.get("webcam_region") or spec.get("webcam_region")):
        layout = None  # no webcam region known for this clip: keep the current layout (honest preview)
    if layout:
        plan["layout"] = layout
    if layout == "split":
        plan["webcam_position"] = p["webcam"]["position"]
        plan["webcam_height_pct"] = p["webcam"]["height_pct"]
    cap = p.get("captions") or {}
    plan["caption_style"] = cap.get("style", plan.get("caption_style"))
    captions = dict(spec.get("captions") or {})
    captions.update({"position": cap.get("position", "low"), "size_pct": cap.get("size_pct", 100),
                     "uppercase": cap.get("uppercase", True)})
    if cap.get("active_color"):
        captions["active_color"] = cap["active_color"]
    if cap.get("emphasis_color"):
        captions["emphasis_color"] = cap["emphasis_color"]
    badge = p.get("badge") or {}
    if badge.get("enabled"):
        plan["badge"] = {"avatar_url": f"/internal/format-profiles/{rec['format_profile_id']}/avatar",
                         "position": badge.get("position", "top_left")}
        plan["title"] = ""  # the badge takes the top: no title overlay over it
        captions.update({"badge_name": badge.get("name", ""), "badge_position": badge.get("position", "top_left")})
    return {**spec, "edit_plan": plan, "captions": captions, "caption_style": plan.get("caption_style")}
