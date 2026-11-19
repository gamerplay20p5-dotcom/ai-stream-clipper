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
    # openvino_vlm = multimodal model (falls back to heuristics when no worker serves it)
    vision_provider: Literal["heuristic", "openvino_vlm"] = "openvino_vlm"
    vision_model: Literal["qwen3-vl-2b-int4", "qwen3-vl-4b-int4", "internvl2-1b-int4"] = "qwen3-vl-2b-int4"
    vision_frame_interval: float = Field(1.0, ge=0.2, le=10.0)
    vision_timeout_seconds: int = Field(300, ge=30, le=3600)
    analysis_context_seconds: float = Field(10.0, ge=0, le=30)  # extra footage around the candidate
    # editorial text: same_as_vision reuses the loaded VLM (no second model in VRAM)
    narrative_model: Literal["qwen3-1.7b-int4", "qwen3-0.6b-int4", "same_as_vision", "template"] = "qwen3-1.7b-int4"
    director_enabled: bool = True
    edit_template: Literal["padrao", "dinamico", "minimalista"] = "padrao"
    normalize_audio: bool = True
    zoom_intensity: float = Field(1.0, ge=0.0, le=1.5)
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
    # Phase 3 - evaluation dataset (human review), retention and quota of its media
    dataset_enabled: bool = True
    dataset_max_per_hour: int = Field(10, ge=0, le=60, description="non-clip examples per session-hour")
    dataset_retention_days: int = Field(21, ge=0, description="media of unreviewed examples; 0 = keep")
    dataset_reviewed_retention_days: int = Field(0, ge=0, description="media of reviewed examples; 0 = keep")
    dataset_quota_gb: float = Field(20.0, ge=0.5, le=2000)
    # Phase 3/5 - temporal memory and narrative continuity
    memory_enabled: bool = True
    max_story_seconds: float = Field(90.0, ge=20, le=180, description="longest clip for a multi-minute story")
    memory_window_seconds: float = Field(180.0, ge=30, le=900)
    merge_gap_seconds: float = Field(20.0, ge=0, le=120)
    # Phase 3 - queue pressure: when the vision queue is this deep, weak candidates skip the VLM
    vision_queue_max: int = Field(6, ge=1, le=200)
    vision_stale_minutes: int = Field(20, ge=2, le=240)
    # Phase 3 - learned ranker: off | shadow (computed and shown, never decides) | active
    ranker_mode: Literal["off", "shadow", "active"] = "shadow"
    # Phase 5 stage B - re-watch: deep second analysis of uncertain clips, post-live retrospective
    deep_review_enabled: bool = True
    deep_review_margin: float = Field(10.0, ge=0, le=50, description="score distance to min_score that triggers it")
    deep_review_max_frames: int = Field(12, ge=6, le=16)
    retrospective_enabled: bool = True
    retrospective_max_per_session: int = Field(5, ge=0, le=30)
    # Phase 6A - quality gate 2.0 (four dimensions, REWORK before rejecting)
    gate_enabled: bool = True
    gate_event_min: float = Field(5.5, ge=0, le=10, description="happening quality to keep (0-10)")
    gate_narrative_min: float = Field(5.0, ge=0, le=10, description="cut quality to be READY (0-10)")
    gate_recovery_min: float = Field(4.0, ge=0, le=10, description="editing potential to try a REWORK")
    gate_max_rework: int = Field(2, ge=0, le=5, description="automatic re-edits per clip")
    # Phase 6B - structural editor (variants are compared before any becomes the main video)
    editor_mode: Literal["off", "shadow", "on"] = "shadow"
    editor_min_gap: float = Field(0.9, ge=0.4, le=3.0, description="silence (s) that can be cut")
    editor_teaser: bool = False
    editor_structure_prefs: dict[str, str] = Field(default_factory=dict, description="category -> A..E | auto")
    editor_variants_per_hour: int = Field(12, ge=0, le=120, description="automatic variant renders per hour")
    # Phase 5 stage E - who is talking (voice embeddings on the audio worker)
    speakers_enabled: bool = True
    # Phase 5 stage E - smart captions
    caption_mode: Literal["streamer_highlight", "streamer_only", "all"] = "streamer_highlight"
    caption_font: Literal["", "Noto Sans", "DejaVu Sans", "Noto Serif"] = ""
    caption_size_pct: int = Field(100, ge=60, le=160)
    caption_position: Literal["low", "middle", "high"] = "low"
    caption_animation: Literal["highlight", "pop", "none"] = "highlight"
    caption_active_color: str = Field("", pattern=r"^(#[0-9a-fA-F]{6})?$")
    caption_emphasis_color: str = Field("", pattern=r"^(#[0-9a-fA-F]{6})?$")
    caption_other_color: str = Field("#9AD7FF", pattern=r"^#[0-9a-fA-F]{6}$")
    # Phase 5 stage E - channel branding (discreet; nothing is added unless filled in)
    brand_name: str = Field("", max_length=40)
    brand_watermark: bool = False
    brand_watermark_position: Literal["top_left", "top_right", "bottom_left"] = "top_right"
    brand_watermark_opacity: float = Field(0.45, ge=0.1, le=1.0)
    brand_cta: str = Field("", max_length=80, description="end-card text, e.g. 'Siga para mais cortes'")
    brand_cta_seconds: float = Field(3.0, ge=1.5, le=6.0)
    brand_hashtags: list[str] = Field(default_factory=list, max_length=8)
    brand_description_footer: str = Field("", max_length=500)
    # Phase 5 - score-based storage lifecycle (media only; rows and labels are always kept)
    cleanup_enabled: bool = False
    cleanup_dry_run: bool = True
    cleanup_keep_min_score: float = Field(50.0, ge=0, le=100, description="editorial score to keep a clip's media")
    review_min_score: float = Field(55.0, ge=0, le=100, description="score to send a clip to human review")
    autopublish_min_score: float = Field(75.0, ge=0, le=100, description="score to consider automatic publishing")
    cleanup_below_min_days: int = Field(3, ge=0, le=365)
    cleanup_rejected_days: int = Field(7, ge=0, le=365)
    cleanup_quarantine_days: int = Field(7, ge=1, le=365)
    cleanup_disposable_quota_gb: float = Field(50.0, ge=1, le=5000)
    cleanup_purge_video: bool = True
    cleanup_purge_source: bool = True
    cleanup_purge_thumbnail: bool = True
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
