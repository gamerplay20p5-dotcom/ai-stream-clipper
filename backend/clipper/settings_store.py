"""Runtime AI / clip settings editable from the dashboard (stored in ``app_settings``)."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, model_validator
from sqlalchemy.orm import Session

from .models import AppSetting

SETTINGS_KEY = "ai"


class ScoreWeights(BaseModel):
    surprise: float = 1.2
    humor: float = 1.0
    intensity: float = 1.3
    visual: float = 0.8
    narrative: float = 1.0
    audience: float = 0.7
    context: float = 0.8
    uniqueness: float = 0.9
    reaction: float = 1.2
    chat: float = 0.5


class AISettings(BaseModel):
    # detection
    sensitivity: float = Field(0.5, ge=0.0, le=1.0, description="0 = conservative, 1 = aggressive")
    min_score: float = Field(55.0, ge=0, le=100)
    language: str = Field("pt", description="ISO code or 'auto'")
    whisper_model: str = "small"
    # clip shaping
    min_clip_seconds: float = Field(15.0, ge=5, le=120)
    max_clip_seconds: float = Field(60.0, ge=10, le=180)
    pre_context_seconds: float = Field(12.0, ge=0, le=60)
    post_context_seconds: float = Field(8.0, ge=0, le=60)
    max_clips_per_hour: int = Field(12, ge=1, le=120)
    dedup_overlap: float = Field(0.35, ge=0.0, le=1.0)
    # vision
    vision_enabled: bool = True
    vision_provider: Literal["heuristic", "openvino_vlm"] = "heuristic"
    vision_frame_interval: float = Field(1.0, ge=0.2, le=10.0)
    vision_timeout_seconds: int = Field(300, ge=30, le=3600)
    # render
    export_preset: Literal["ultrafast", "veryfast", "faster", "fast", "medium", "slow"] = "veryfast"
    export_crf: int = Field(21, ge=14, le=32)
    export_fps: int = Field(30, ge=24, le=60)
    caption_style: Literal["bold", "clean", "boxed", "none"] = "bold"
    default_layout: Literal["auto", "gameplay", "split", "zoom"] = "auto"
    show_title_overlay: bool = True
    # processing limits
    max_parallel_renders: int = Field(2, ge=1, le=16)
    max_parallel_audio: int = Field(4, ge=1, le=32)
    # retention (days, 0 = keep forever)
    retention_rejected_days: int = Field(3, ge=0)
    retention_unreviewed_days: int = Field(14, ge=0)
    retention_approved_days: int = Field(0, ge=0)
    # auto-publish is a future module: always disabled in this build
    auto_publish: Literal[False] = False
    weights: ScoreWeights = Field(default_factory=ScoreWeights)

    @model_validator(mode="after")
    def _check_durations(self):
        if self.min_clip_seconds >= self.max_clip_seconds:
            raise ValueError("min_clip_seconds must be lower than max_clip_seconds")
        return self


def load_ai_settings(db: Session) -> AISettings:
    row = db.get(AppSetting, SETTINGS_KEY)
    if row is None or not row.value:
        return AISettings()
    try:
        return AISettings.model_validate(row.value)
    except Exception:
        return AISettings()


def save_ai_settings(db: Session, settings: AISettings) -> AISettings:
    row = db.get(AppSetting, SETTINGS_KEY)
    if row is None:
        row = AppSetting(key=SETTINGS_KEY, value=settings.model_dump())
        db.add(row)
    else:
        row.value = settings.model_dump()
    return settings
