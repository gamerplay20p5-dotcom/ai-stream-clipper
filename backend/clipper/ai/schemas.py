"""Validated contracts exchanged between AI components, workers, the API and the renderer.

All times are **clip-window relative seconds** unless the field name ends with ``_abs``
(session offsets). Model outputs are always parsed into these schemas; anything that does
not validate is discarded, never "repaired" by guessing.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator

# ---------------------------------------------------------------------------
# Visual (multimodal) analysis
# ---------------------------------------------------------------------------


class VisualEvent(BaseModel):
    t: float = Field(ge=0, description="seconds from the start of the analysed window")
    label: str = Field(max_length=160)
    importance: float = Field(ge=0, le=10)

    @field_validator("label")
    @classmethod
    def _strip(cls, v: str) -> str:
        return v.strip()


class NarrativeArc(BaseModel):
    setup_start: float | None = Field(None, ge=0)
    climax: float | None = Field(None, ge=0)
    resolution_end: float | None = Field(None, ge=0)
    complete: bool = False  # setup + development + outcome all visible

    @model_validator(mode="after")
    def _ordered(self):
        pts = [p for p in (self.setup_start, self.climax, self.resolution_end) if p is not None]
        if pts != sorted(pts):
            raise ValueError("arc points must be ordered setup <= climax <= resolution")
        return self


class VisualAnalysis(BaseModel):
    """Answer of the multimodal model (or of the heuristic provider) for one candidate."""

    provider: str  # e.g. "openvino-vlm:qwen3-vl-2b-int4@GPU" or "heuristic-opencv"
    model: str = ""
    device: str = ""
    summary: str = Field("", max_length=600)  # what happened in the gameplay
    key_event: str = Field("", max_length=300)  # what deserves attention
    funny: float = Field(0, ge=0, le=10)
    unexpected: float = Field(0, ge=0, le=10)
    important: float = Field(0, ge=0, le=10)
    action: float = Field(0, ge=0, le=10)
    events: list[VisualEvent] = Field(default_factory=list, max_length=12)
    arc: NarrativeArc = Field(default_factory=NarrativeArc)
    preserve: tuple[float, float] | None = None  # window that must stay in the clip
    reaction_linked: bool | None = None  # streamer reaction relates to the on-screen event
    confidence: float = Field(0.5, ge=0, le=1)
    frame_times: list[float] = Field(default_factory=list)  # timestamps actually shown to the model
    window_abs: tuple[float, float] = (0.0, 0.0)  # session offsets of the analysed window
    latency_s: float = 0.0
    fallback: bool = False  # True when the multimodal model was unavailable
    notes: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _within_window(self):
        span = self.window_abs[1] - self.window_abs[0]
        if span > 0:
            for e in self.events:
                if e.t > span + 0.5:
                    raise ValueError(f"event at {e.t}s outside analysed window ({span:.1f}s)")
            if self.preserve and (self.preserve[0] > self.preserve[1] or self.preserve[1] > span + 0.5):
                raise ValueError("preserve window outside analysed window")
        return self

    @property
    def interest(self) -> float:
        # "important" is excluded: small VLMs rate almost everything as important (measured)
        return max(self.funny, self.unexpected, self.action)


# ---------------------------------------------------------------------------
# Acoustic classification
# ---------------------------------------------------------------------------

AcousticCategory = Literal["speech", "laughter", "scream", "shout", "game_impact", "gunfire", "vehicle",
                           "music", "crowd", "silence", "other"]


class AcousticFrame(BaseModel):
    t: float  # session offset (s)
    category: AcousticCategory
    label: str
    score: float = Field(ge=0, le=1)


# ---------------------------------------------------------------------------
# Narrative (text model)
# ---------------------------------------------------------------------------


class NarrativeResult(BaseModel):
    provider: str
    justification: str = Field("", max_length=600)
    titles: list[str] = Field(default_factory=list, max_length=5)
    hook: str = Field("", max_length=160)
    description: str = Field("", max_length=400)
    hashtags: list[str] = Field(default_factory=list, max_length=6)
    latency_s: float = 0.0
    fallback: bool = False

    @field_validator("titles")
    @classmethod
    def _clean_titles(cls, v: list[str]) -> list[str]:
        out = []
        for t in v:
            t = " ".join(str(t).split()).strip("\"' ")
            if 3 <= len(t) <= 100:
                out.append(t)
        return out


# ---------------------------------------------------------------------------
# Director decision + edit plan
# ---------------------------------------------------------------------------


class Evidence(BaseModel):
    source: Literal["audio", "transcript", "acoustic", "visual", "heuristic", "narrative"]
    t_abs: float | None = None
    detail: str = Field(max_length=240)


class DirectorDecision(BaseModel):
    start_abs: float
    end_abs: float
    peak_abs: float
    score: float = Field(ge=0, le=100)
    base_score: float = Field(ge=0, le=100)  # heuristic score before multimodal fusion
    keep: bool = True
    mode: Literal["multimodal", "audio_only"] = "audio_only"
    reasons: list[str] = Field(default_factory=list)
    evidence: list[Evidence] = Field(default_factory=list)
    adjustments: list[str] = Field(default_factory=list)  # how/why bounds moved

    @model_validator(mode="after")
    def _bounds(self):
        if not self.start_abs < self.end_abs:
            raise ValueError("invalid clip bounds")
        self.peak_abs = min(max(self.peak_abs, self.start_abs), self.end_abs)
        return self


class CropKeyframe(BaseModel):
    t: float = Field(ge=0)  # clip-relative
    cx: float = Field(ge=0, le=1)  # horizontal centre of the crop (normalised)


class ZoomMoment(BaseModel):
    t: float = Field(ge=0)  # clip-relative start of the punch-in
    duration: float = Field(gt=0, le=4)
    scale: float = Field(ge=1.0, le=1.35)  # moderate zoom only


class EditPlan(BaseModel):
    version: int = 1
    template: str = "padrao"
    layout: Literal["gameplay", "zoom", "split", "action"] = "gameplay"
    webcam_region: dict | None = None
    face_track: list[dict] = Field(default_factory=list)  # [{t, x, y, w, h}] clip-relative, normalised
    crop_keyframes: list[CropKeyframe] = Field(default_factory=list, max_length=24)
    zooms: list[ZoomMoment] = Field(default_factory=list, max_length=3)
    caption_style: Literal["bold", "clean", "boxed", "none"] = "bold"
    emphasis_words: list[str] = Field(default_factory=list, max_length=30)
    fade_in: float = Field(0.2, ge=0, le=1.0)
    fade_out: float = Field(0.35, ge=0, le=1.5)
    normalize_audio: bool = True
    target_lufs: float = Field(-14.0, ge=-24, le=-9)
    title: str = ""
    base_start_abs: float = 0.0  # session offset that clip-relative times refer to
    rationale: list[str] = Field(default_factory=list)
    source: Literal["ai", "heuristic", "user"] = "heuristic"
