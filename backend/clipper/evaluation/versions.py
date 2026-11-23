"""Provenance: which code, models and settings produced a decision (for reproducible evaluation)."""

from __future__ import annotations

import hashlib
import json

from .. import __version__
from ..db import utcnow

# settings that change which moments become clips or how they are judged
_DECISION_KEYS = ("sensitivity", "min_score", "pre_context_seconds", "post_context_seconds", "max_clips_per_hour",
                  "dedup_overlap", "min_clip_seconds", "max_clip_seconds", "vision_enabled", "vision_provider",
                  "vision_model", "narrative_model", "director_enabled", "analysis_context_seconds", "weights",
                  "memory_window_seconds", "merge_gap_seconds", "visual_discovery", "multimodal_scoring",
                  "grounded_metadata")


def settings_snapshot(settings) -> dict:
    d = settings.model_dump() if hasattr(settings, "model_dump") else dict(settings)
    return {k: d[k] for k in _DECISION_KEYS if k in d}


def settings_hash(settings) -> str:
    raw = json.dumps(settings_snapshot(settings), sort_keys=True, default=str).encode()
    return hashlib.sha1(raw).hexdigest()[:12]


def scoring_provenance(settings, scoring_version: str) -> dict:
    return {"app": __version__, "scoring": scoring_version, "settings_hash": settings_hash(settings),
            "settings": settings_snapshot(settings), "at": utcnow().isoformat() + "Z"}


def provider_of(clip) -> str:
    """Which kind of judgement produced the clip's final decision."""
    meta = clip.analysis_meta or {}
    if (clip.decision or {}).get("ranker"):
        return "ranker"
    if meta.get("vision_provider", "").startswith("openvino-vlm") and not meta.get("fallback_reason"):
        return "multimodal"
    if clip.analysis is not None or meta:
        return "audio_fallback" if meta.get("fallback_reason") else "heuristic"
    return "heuristic"
