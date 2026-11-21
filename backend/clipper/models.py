"""ORM models.

All timestamps are naive UTC. Media offsets (``start``, ``end``, ``t``) are seconds
relative to the start of a :class:`StreamSession`.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base, utcnow


class User(Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255), default="")
    # admin = HTS staff (whole panel); viewer = external account (ONLY the clips shared with it).
    # Never admin by default: an account is admin only when someone sets it explicitly.
    role: Mapped[str] = mapped_column(String(20), default="viewer")
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    # bumped on deactivation, role change, password reset or "end sessions": every token issued
    # before stops working on the next request (tokens carry the version, the DB has the truth)
    session_version: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class PasswordToken(Base):
    """Single-use password link (invite or reset). Only the SHA-256 of the token is stored."""
    __tablename__ = "password_tokens"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    purpose: Mapped[str] = mapped_column(String(16))  # invite | reset
    created_by: Mapped[str] = mapped_column(String(255), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(DateTime)
    used_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class Streamer(Base):
    __tablename__ = "streamers"
    __table_args__ = (UniqueConstraint("platform", "channel", name="uq_streamer_platform_channel"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    platform: Mapped[str] = mapped_column(String(20))  # twitch | youtube | file
    channel: Mapped[str] = mapped_column(String(255))
    url: Mapped[str] = mapped_column(String(1024))
    display_name: Mapped[str] = mapped_column(String(255), default="")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    preferences: Mapped[dict] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(20), default="unknown")  # unknown|offline|live|error
    stream_title: Mapped[str] = mapped_column(String(512), default="")
    last_checked_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_live_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_error: Mapped[str] = mapped_column(Text, default="")
    check_requested: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    sessions: Mapped[list[StreamSession]] = relationship(back_populates="streamer", cascade="all, delete-orphan")


class StreamSession(Base):
    __tablename__ = "stream_sessions"
    id: Mapped[int] = mapped_column(primary_key=True)
    streamer_id: Mapped[int] = mapped_column(ForeignKey("streamers.id", ondelete="CASCADE"), index=True)
    status: Mapped[str] = mapped_column(String(20), default="live", index=True)  # live|ended|failed
    title: Mapped[str] = mapped_column(String(512), default="")
    started_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    end_reason: Mapped[str] = mapped_column(String(255), default="")
    capture_worker_id: Mapped[str] = mapped_column(String(128), default="")
    segment_base_url: Mapped[str] = mapped_column(String(512), default="")
    covered_until: Mapped[float] = mapped_column(Float, default=0.0)  # end of last captured segment
    analyzed_until: Mapped[float] = mapped_column(Float, default=0.0)  # contiguous analysed prefix
    scored_until: Mapped[float] = mapped_column(Float, default=0.0)
    segment_count: Mapped[int] = mapped_column(Integer, default=0)
    stop_requested: Mapped[bool] = mapped_column(Boolean, default=False)
    # scoring diagnostics: rejected moments by reason, best rejected score, last scoring time
    stats: Mapped[dict] = mapped_column(JSON, default=dict)

    streamer: Mapped[Streamer] = relationship(back_populates="sessions")


class Segment(Base):
    __tablename__ = "segments"
    __table_args__ = (UniqueConstraint("session_id", "idx", name="uq_segment_session_idx"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    session_id: Mapped[int] = mapped_column(ForeignKey("stream_sessions.id", ondelete="CASCADE"), index=True)
    idx: Mapped[int] = mapped_column(Integer)
    run_id: Mapped[int] = mapped_column(Integer, default=0)  # increments on every reconnect
    start: Mapped[float] = mapped_column(Float)
    duration: Mapped[float] = mapped_column(Float)
    size: Mapped[int] = mapped_column(Integer, default=0)
    sha256: Mapped[str] = mapped_column(String(64), default="")
    captured_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    available: Mapped[bool] = mapped_column(Boolean, default=True)
    analysis_state: Mapped[str] = mapped_column(String(16), default="pending")  # pending|done|skipped


class AudioFeature(Base):
    """Per-segment acoustic summary (posted by the audio worker)."""

    __tablename__ = "audio_features"
    __table_args__ = (UniqueConstraint("session_id", "segment_idx", name="uq_audiofeat_session_idx"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    session_id: Mapped[int] = mapped_column(ForeignKey("stream_sessions.id", ondelete="CASCADE"), index=True)
    segment_idx: Mapped[int] = mapped_column(Integer)
    start: Mapped[float] = mapped_column(Float)
    hop: Mapped[float] = mapped_column(Float)  # seconds per value
    loudness_db: Mapped[list] = mapped_column(JSON, default=list)
    centroid_hz: Mapped[list] = mapped_column(JSON, default=list)
    flux: Mapped[list] = mapped_column(JSON, default=list)
    # Phase 2: {"yamnet": {"hop", "cats": {cat: [...]}}, "pitch_hz": [...], "pitch_hop": 0.25}
    extra: Mapped[dict] = mapped_column(JSON, default=dict)
    speech_ratio: Mapped[float] = mapped_column(Float, default=0.0)
    language: Mapped[str] = mapped_column(String(8), default="")


class TranscriptWord(Base):
    __tablename__ = "transcript_words"
    __table_args__ = (Index("ix_words_session_start", "session_id", "start"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    session_id: Mapped[int] = mapped_column(ForeignKey("stream_sessions.id", ondelete="CASCADE"))
    segment_idx: Mapped[int] = mapped_column(Integer, index=True)
    start: Mapped[float] = mapped_column(Float)
    end: Mapped[float] = mapped_column(Float)
    word: Mapped[str] = mapped_column(String(128))
    probability: Mapped[float] = mapped_column(Float, default=1.0)
    speaker: Mapped[str] = mapped_column(String(16), default="")  # streamer | other:N | unknown | overlap | ""


class Event(Base):
    __tablename__ = "events"
    __table_args__ = (Index("ix_events_session_t", "session_id", "t"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    session_id: Mapped[int] = mapped_column(ForeignKey("stream_sessions.id", ondelete="CASCADE"))
    t: Mapped[float] = mapped_column(Float)
    duration: Mapped[float] = mapped_column(Float, default=0.0)
    kind: Mapped[str] = mapped_column(String(32))
    strength: Mapped[float] = mapped_column(Float, default=0.0)
    data: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class Clip(Base):
    __tablename__ = "clips"
    id: Mapped[int] = mapped_column(primary_key=True)
    session_id: Mapped[int] = mapped_column(ForeignKey("stream_sessions.id", ondelete="CASCADE"), index=True)
    streamer_id: Mapped[int] = mapped_column(ForeignKey("streamers.id", ondelete="CASCADE"), index=True)
    # candidate -> (vision) -> rendering -> ready -> approved/rejected ; failed
    status: Mapped[str] = mapped_column(String(20), default="candidate", index=True)
    start: Mapped[float] = mapped_column(Float)
    end: Mapped[float] = mapped_column(Float)
    peak: Mapped[float] = mapped_column(Float)
    score: Mapped[float] = mapped_column(Float, default=0.0)
    score_breakdown: Mapped[dict] = mapped_column(JSON, default=dict)
    reasons: Mapped[list] = mapped_column(JSON, default=list)
    kinds: Mapped[list] = mapped_column(JSON, default=list)  # event kinds that triggered the clip
    transcript: Mapped[str] = mapped_column(Text, default="")
    title: Mapped[str] = mapped_column(String(255), default="")
    description: Mapped[str] = mapped_column(Text, default="")
    title_suggestions: Mapped[list] = mapped_column(JSON, default=list)
    hashtags: Mapped[list] = mapped_column(JSON, default=list)
    caption_style: Mapped[str] = mapped_column(String(32), default="bold")
    layout: Mapped[str] = mapped_column(String(32), default="auto")
    webcam_region: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    vision: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    vision_state: Mapped[str] = mapped_column(String(16), default="none")  # none|pending|done|skipped
    # Phase 2 - multimodal analysis, director decision and structured edit plan
    analysis: Mapped[dict | None] = mapped_column(JSON, nullable=True)  # VisualAnalysis + narrative
    decision: Mapped[dict | None] = mapped_column(JSON, nullable=True)  # DirectorDecision
    edit_plan: Mapped[dict | None] = mapped_column(JSON, nullable=True)  # EditPlan
    analysis_meta: Mapped[dict | None] = mapped_column(JSON, nullable=True)  # models, devices, timings, fallback
    analysis_runs: Mapped[int] = mapped_column(Integer, default=0)
    is_test: Mapped[bool] = mapped_column(Boolean, default=False, index=True)  # TESTE MANUAL: excluded from metrics
    heuristic_start: Mapped[float | None] = mapped_column(Float, nullable=True)  # Phase-1 bounds, kept for comparison
    heuristic_end: Mapped[float | None] = mapped_column(Float, nullable=True)
    # Phase 3: versions of code/models/settings that produced the clip
    provenance: Mapped[dict] = mapped_column(JSON, default=dict)
    # Phase 5: editorial judgement (preliminary at detection, final after review) and story link
    gate: Mapped[str] = mapped_column(String(20), default="", index=True)  # Phase 6A outcome
    prelim_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    editorial_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    situation_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    # Phase 5: storage lifecycle (media only - the row and the labels are never deleted by cleanup)
    protected: Mapped[bool] = mapped_column(Boolean, default=False)
    favorite: Mapped[bool] = mapped_column(Boolean, default=False)
    lifecycle: Mapped[str] = mapped_column(String(16), default="active", index=True)  # active|quarantined|purged
    quarantined_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    purge_after: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    purge_reason: Mapped[str] = mapped_column(String(255), default="")
    media_purged_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    version: Mapped[int] = mapped_column(Integer, default=0)
    source_path: Mapped[str] = mapped_column(String(512), default="")
    source_start: Mapped[float] = mapped_column(Float, default=0.0)
    source_duration: Mapped[float] = mapped_column(Float, default=0.0)
    video_path: Mapped[str] = mapped_column(String(512), default="")
    thumb_path: Mapped[str] = mapped_column(String(512), default="")
    file_size: Mapped[int] = mapped_column(Integer, default=0)
    sha256: Mapped[str] = mapped_column(String(64), default="")
    render_seconds: Mapped[float] = mapped_column(Float, default=0.0)
    error: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)

    @property
    def duration(self) -> float:
        return max(0.0, self.end - self.start)


class Job(Base):
    __tablename__ = "jobs"
    __table_args__ = (Index("ix_jobs_claim", "status", "type", "run_after"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    type: Mapped[str] = mapped_column(String(32), index=True)
    status: Mapped[str] = mapped_column(String(16), default="queued")  # queued|running|done|dead|cancelled
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    result: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    priority: Mapped[int] = mapped_column(Integer, default=100)  # lower runs first
    requires_gpu: Mapped[bool] = mapped_column(Boolean, default=False)
    idempotency_key: Mapped[str | None] = mapped_column(String(255), unique=True, nullable=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, default=4)
    run_after: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    worker_id: Mapped[str] = mapped_column(String(128), default="")
    last_error: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class Worker(Base):
    __tablename__ = "workers"
    id: Mapped[str] = mapped_column(String(128), primary_key=True)
    host: Mapped[str] = mapped_column(String(128), default="")
    host_label: Mapped[str] = mapped_column(String(64), default="")
    roles: Mapped[list] = mapped_column(JSON, default=list)
    capabilities: Mapped[dict] = mapped_column(JSON, default=dict)
    base_url: Mapped[str] = mapped_column(String(512), default="")
    version: Mapped[str] = mapped_column(String(32), default="")
    status: Mapped[str] = mapped_column(String(16), default="online")  # online|offline
    metrics: Mapped[dict] = mapped_column(JSON, default=dict)
    started_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    last_heartbeat_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class AuditLog(Base):
    __tablename__ = "audit_log"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_email: Mapped[str] = mapped_column(String(255), default="")
    action: Mapped[str] = mapped_column(String(64))
    target: Mapped[str] = mapped_column(String(255), default="")
    details: Mapped[dict] = mapped_column(JSON, default=dict)
    ip: Mapped[str] = mapped_column(String(64), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)


class AppSetting(Base):
    __tablename__ = "app_settings"
    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[dict] = mapped_column(JSON, default=dict)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


# ---------------------------------------------------------------------------
# Phase 3 - editorial evaluation, human feedback and learning
# ---------------------------------------------------------------------------


class Candidate(Base):
    """Every moment the scorer evaluated (accepted or not), with its observable features.
    Rejected moments are what makes false negatives, active learning and a ranker possible."""

    __tablename__ = "candidates"
    __table_args__ = (Index("ix_candidates_session_peak", "session_id", "peak"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    session_id: Mapped[int] = mapped_column(ForeignKey("stream_sessions.id", ondelete="CASCADE"))
    streamer_id: Mapped[int] = mapped_column(ForeignKey("streamers.id", ondelete="CASCADE"), index=True)
    peak: Mapped[float] = mapped_column(Float)
    start: Mapped[float | None] = mapped_column(Float, nullable=True)
    end: Mapped[float | None] = mapped_column(Float, nullable=True)
    score: Mapped[float | None] = mapped_column(Float, nullable=True)
    decision: Mapped[str] = mapped_column(String(24), index=True)  # accepted | text_only | below_min_score | ...
    kinds: Mapped[list] = mapped_column(JSON, default=list)
    breakdown: Mapped[dict] = mapped_column(JSON, default=dict)
    features: Mapped[dict] = mapped_column(JSON, default=dict)
    transcript: Mapped[str] = mapped_column(Text, default="")
    situation: Mapped[str] = mapped_column(String(64), default="")  # temporal memory label at the peak
    clip_id: Mapped[int | None] = mapped_column(ForeignKey("clips.id", ondelete="SET NULL"), nullable=True, index=True)
    scoring_version: Mapped[str] = mapped_column(String(32), default="")
    ranker_score: Mapped[float | None] = mapped_column(Float, nullable=True)  # shadow ranker probability
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)


class EvalExample(Base):
    """A reviewable example of the evaluation dataset: a frozen snapshot of what the system saw
    and decided, plus its own (re-encoded) media so it outlives the capture buffer and the clip."""

    __tablename__ = "eval_examples"
    id: Mapped[int] = mapped_column(primary_key=True)
    candidate_id: Mapped[int | None] = mapped_column(ForeignKey("candidates.id", ondelete="SET NULL"), nullable=True,
                                                     unique=True)
    clip_id: Mapped[int | None] = mapped_column(ForeignKey("clips.id", ondelete="SET NULL"), nullable=True, index=True)
    session_id: Mapped[int | None] = mapped_column(ForeignKey("stream_sessions.id", ondelete="SET NULL"), nullable=True,
                                                   index=True)
    streamer_id: Mapped[int | None] = mapped_column(ForeignKey("streamers.id", ondelete="SET NULL"), nullable=True)
    streamer_name: Mapped[str] = mapped_column(String(255), default="")
    platform: Mapped[str] = mapped_column(String(20), default="")
    stratum: Mapped[str] = mapped_column(String(32), index=True)  # why it was selected (active learning)
    priority: Mapped[float] = mapped_column(Float, default=0.0)  # higher = more useful to review now
    split: Mapped[str] = mapped_column(String(8), default="train", index=True)  # train | val (by session)
    is_test: Mapped[bool] = mapped_column(Boolean, default=False)  # manual test clip: never a training positive
    # window of the media (session seconds) and the AI decision inside it
    media_start: Mapped[float] = mapped_column(Float, default=0.0)
    media_end: Mapped[float] = mapped_column(Float, default=0.0)
    ai: Mapped[dict] = mapped_column(JSON, default=dict)  # score, decision, bounds, breakdown, dimensions, provider
    context: Mapped[dict] = mapped_column(JSON, default=dict)  # transcript, acoustic events, visual, memory
    provenance: Mapped[dict] = mapped_column(JSON, default=dict)  # versions of code, models, settings
    ai_runs: Mapped[list] = mapped_column(JSON, default=list)  # re-evaluations with newer AI versions
    media_state: Mapped[str] = mapped_column(String(16), default="pending", index=True)  # pending|ready|failed|expired
    media_path: Mapped[str] = mapped_column(String(512), default="")
    media_bytes: Mapped[int] = mapped_column(Integer, default=0)
    media_error: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class Review(Base):
    """Human editorial judgement of an example (one current review per reviewer)."""

    __tablename__ = "reviews"
    __table_args__ = (UniqueConstraint("example_id", "reviewer", name="uq_review_example_reviewer"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    example_id: Mapped[int] = mapped_column(ForeignKey("eval_examples.id", ondelete="CASCADE"), index=True)
    reviewer: Mapped[str] = mapped_column(String(255))
    rating: Mapped[str] = mapped_column(String(24))  # excellent|good|fair|bad|should_not_exist
    category: Mapped[str] = mapped_column(String(32), default="")  # what kind of moment it is
    editorial_reasons: Mapped[list] = mapped_column(JSON, default=list)
    technical_issues: Mapped[list] = mapped_column(JSON, default=list)
    description: Mapped[str] = mapped_column(Text, default="")  # objective description of what happens
    notes: Mapped[str] = mapped_column(Text, default="")
    context_quality: Mapped[int | None] = mapped_column(Integer, nullable=True)  # 0-10
    ideal_duration: Mapped[float | None] = mapped_column(Float, nullable=True)
    # human timestamps (session seconds)
    event_start: Mapped[float | None] = mapped_column(Float, nullable=True)
    event_peak: Mapped[float | None] = mapped_column(Float, nullable=True)
    event_end: Mapped[float | None] = mapped_column(Float, nullable=True)
    short_start: Mapped[float | None] = mapped_column(Float, nullable=True)
    short_end: Mapped[float | None] = mapped_column(Float, nullable=True)
    ai_snapshot: Mapped[dict] = mapped_column(JSON, default=dict)  # the AI result that was judged
    source: Mapped[str] = mapped_column(String(16), default="review")  # review | clip_action (approve/reject)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class RankerModel(Base):
    """A trained lightweight ranker. Never used for decisions until an admin promotes it after
    validation; production keeps the heuristic ranking otherwise."""

    __tablename__ = "ranker_models"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(64))
    kind: Mapped[str] = mapped_column(String(32), default="logistic")
    status: Mapped[str] = mapped_column(String(16), default="candidate", index=True)  # candidate|shadow|active|archived
    feature_names: Mapped[list] = mapped_column(JSON, default=list)
    params: Mapped[dict] = mapped_column(JSON, default=dict)  # weights, normalisation, threshold
    metrics: Mapped[dict] = mapped_column(JSON, default=dict)  # validation metrics vs heuristic baseline
    n_train: Mapped[int] = mapped_column(Integer, default=0)
    n_val: Mapped[int] = mapped_column(Integer, default=0)
    created_by: Mapped[str] = mapped_column(String(255), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    activated_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


# ---------------------------------------------------------------------------
# Phase 4 - auto publisher (YouTube Shorts, TikTok, Instagram Reels)
# ---------------------------------------------------------------------------


class PublishApp(Base):
    """OAuth client registered by the admin on each platform (secret encrypted at rest)."""

    __tablename__ = "publish_apps"
    platform: Mapped[str] = mapped_column(String(20), primary_key=True)  # youtube | tiktok | instagram
    client_id: Mapped[str] = mapped_column(String(512), default="")
    client_secret_enc: Mapped[str] = mapped_column(Text, default="")
    redirect_uri: Mapped[str] = mapped_column(String(1024), default="")
    config_id: Mapped[str] = mapped_column(String(64), default="")  # Meta Unified Login configuration (not a secret)
    updated_by: Mapped[str] = mapped_column(String(255), default="")
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class OAuthState(Base):
    __tablename__ = "oauth_states"
    state: Mapped[str] = mapped_column(String(64), primary_key=True)
    platform: Mapped[str] = mapped_column(String(20))
    verifier_enc: Mapped[str] = mapped_column(Text, default="")  # PKCE code verifier (encrypted)
    created_by: Mapped[str] = mapped_column(String(255), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class PublishAccount(Base):
    __tablename__ = "publish_accounts"
    __table_args__ = (UniqueConstraint("platform", "external_id", name="uq_publish_account"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    platform: Mapped[str] = mapped_column(String(20), index=True)
    external_id: Mapped[str] = mapped_column(String(255))  # channel id / open_id / ig user id
    name: Mapped[str] = mapped_column(String(255), default="")
    handle: Mapped[str] = mapped_column(String(255), default="")
    tokens_enc: Mapped[str] = mapped_column(Text, default="")  # access + refresh tokens (encrypted JSON)
    scopes: Mapped[list] = mapped_column(JSON, default=list)
    status: Mapped[str] = mapped_column(String(16), default="connected")  # connected|expired|revoked|error
    token_expires_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    meta: Mapped[dict] = mapped_column(JSON, default=dict)  # platform capabilities (privacy options, limits)
    quota: Mapped[dict] = mapped_column(JSON, default=dict)  # e.g. {"day": "2026-11-18", "units": 3200}
    last_error: Mapped[str] = mapped_column(Text, default="")
    connected_by: Mapped[str] = mapped_column(String(255), default="")
    connected_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class PublishRule(Base):
    """How clips of a streamer (or of all streamers) go to an account."""

    __tablename__ = "publish_rules"
    id: Mapped[int] = mapped_column(primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("publish_accounts.id", ondelete="CASCADE"), index=True)
    streamer_id: Mapped[int | None] = mapped_column(ForeignKey("streamers.id", ondelete="CASCADE"), nullable=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    mode: Mapped[str] = mapped_column(String(16), default="manual")  # manual | scheduled | immediate
    require_approval: Mapped[bool] = mapped_column(Boolean, default=True)  # human approval before publishing
    min_score: Mapped[float] = mapped_column(Float, default=70.0)
    privacy: Mapped[str] = mapped_column(String(16), default="private")  # private | unlisted | public
    allow_public_auto: Mapped[bool] = mapped_column(Boolean, default=False)  # explicit admin opt-in
    max_per_day: Mapped[int] = mapped_column(Integer, default=3)
    slots: Mapped[list] = mapped_column(JSON, default=list)  # ["12:00", "18:30"] local time
    per_slot: Mapped[int] = mapped_column(Integer, default=1, server_default="1")  # videos sharing one slot
    timezone: Mapped[str] = mapped_column(String(64), default="America/Sao_Paulo")
    templates: Mapped[dict] = mapped_column(JSON, default=dict)  # title/description/hashtags per platform
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class Publication(Base):
    """One clip on one account. Unique per (clip, account): a clip is never uploaded twice there."""

    __tablename__ = "publications"
    __table_args__ = (UniqueConstraint("clip_id", "account_id", name="uq_publication_clip_account"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    clip_id: Mapped[int | None] = mapped_column(ForeignKey("clips.id", ondelete="SET NULL"), nullable=True, index=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("publish_accounts.id", ondelete="CASCADE"), index=True)
    rule_id: Mapped[int | None] = mapped_column(ForeignKey("publish_rules.id", ondelete="SET NULL"), nullable=True)
    platform: Mapped[str] = mapped_column(String(20), index=True)
    # draft -> scheduled -> uploading -> processing -> published | failed | cancelled
    status: Mapped[str] = mapped_column(String(16), default="draft", index=True)
    origin: Mapped[str] = mapped_column(String(16), default="manual")  # manual | auto
    scheduled_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, index=True)
    title: Mapped[str] = mapped_column(String(255), default="")
    description: Mapped[str] = mapped_column(Text, default="")
    tags: Mapped[list] = mapped_column(JSON, default=list)
    privacy: Mapped[str] = mapped_column(String(16), default="private")
    video_sha256: Mapped[str] = mapped_column(String(64), default="")
    video_bytes: Mapped[int] = mapped_column(Integer, default=0)
    # Phase 6B: immutable media snapshot - the exact file approved/scheduled (never "whatever is current")
    video_path: Mapped[str] = mapped_column(String(512), default="")
    upload_state: Mapped[dict] = mapped_column(JSON, default=dict)  # resumable session (never tokens)
    remote_id: Mapped[str] = mapped_column(String(255), default="")
    remote_url: Mapped[str] = mapped_column(String(1024), default="")
    remote_status: Mapped[dict] = mapped_column(JSON, default=dict)
    metrics: Mapped[dict] = mapped_column(JSON, default=dict)
    metrics_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    last_error: Mapped[str] = mapped_column(Text, default="")
    log: Mapped[list] = mapped_column(JSON, default=list)  # [{"at", "event", "detail"}]
    created_by: Mapped[str] = mapped_column(String(255), default="")
    published_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


# ---------------------------------------------------------------------------
# Phase 5 - story memory, cleanup audit trail, learning runs
# ---------------------------------------------------------------------------


class SituationRow(Base):
    """A narrative situation followed over minutes (see ai/story.py). Bounded JSON payload."""

    __tablename__ = "situations"
    __table_args__ = (Index("ix_situations_session_start", "session_id", "start"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    session_id: Mapped[int] = mapped_column(ForeignKey("stream_sessions.id", ondelete="CASCADE"))
    kind: Mapped[str] = mapped_column(String(32), index=True)
    start: Mapped[float] = mapped_column(Float)
    last: Mapped[float] = mapped_column(Float)
    status: Mapped[str] = mapped_column(String(16), default="ongoing")
    confidence: Mapped[float] = mapped_column(Float, default=0.0)
    summary: Mapped[str] = mapped_column(Text, default="")
    data: Mapped[dict] = mapped_column(JSON, default=dict)
    related_to: Mapped[int | None] = mapped_column(Integer, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class CleanupLog(Base):
    """Every storage action (quarantine, restore, purge of a given artefact) - idempotent trail."""

    __tablename__ = "cleanup_log"
    id: Mapped[int] = mapped_column(primary_key=True)
    clip_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    action: Mapped[str] = mapped_column(String(24))  # quarantine | restore | purge_video | purge_source | ...
    reason: Mapped[str] = mapped_column(String(255), default="")
    bytes_freed: Mapped[int] = mapped_column(Integer, default=0)
    dry_run: Mapped[bool] = mapped_column(Boolean, default=False)
    actor: Mapped[str] = mapped_column(String(255), default="system")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)


class LearningRun(Base):
    """Background learning job: preference profile + (re)training attempt and its outcome."""

    __tablename__ = "learning_runs"
    id: Mapped[int] = mapped_column(primary_key=True)
    labels: Mapped[int] = mapped_column(Integer, default=0)
    outcome: Mapped[str] = mapped_column(String(32), default="")  # profile_only | trained_shadow | promoted | ...
    ranker_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    profile: Mapped[dict] = mapped_column(JSON, default=dict)
    detail: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)


# ---------------------------------------------------------------------------
# Phase 5 stage E - who is talking
# ---------------------------------------------------------------------------


class SpeechTurn(Base):
    """One spoken sentence with its voice embedding and the assigned speaker label."""

    __tablename__ = "speech_turns"
    __table_args__ = (Index("ix_speech_turns_session_start", "session_id", "start"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    session_id: Mapped[int] = mapped_column(ForeignKey("stream_sessions.id", ondelete="CASCADE"))
    segment_idx: Mapped[int] = mapped_column(Integer)
    start: Mapped[float] = mapped_column(Float)
    end: Mapped[float] = mapped_column(Float)
    emb: Mapped[list] = mapped_column(JSON, default=list)
    label: Mapped[str] = mapped_column(String(16), default="unknown")
    confidence: Mapped[float] = mapped_column(Float, default=0.0)
    text: Mapped[str] = mapped_column(Text, default="")


class SpeakerProfile(Base):
    """The streamer's voice: a hypothesis (most speech time) until the owner confirms it."""

    __tablename__ = "speaker_profiles"
    id: Mapped[int] = mapped_column(primary_key=True)
    streamer_id: Mapped[int] = mapped_column(ForeignKey("streamers.id", ondelete="CASCADE"), unique=True)
    centroid: Mapped[list] = mapped_column(JSON, default=list)
    n: Mapped[int] = mapped_column(Integer, default=0)
    confirmed: Mapped[bool] = mapped_column(Boolean, default=False)
    source: Mapped[str] = mapped_column(String(32), default="auto")  # auto | confirmed_cluster | reference
    sample: Mapped[dict] = mapped_column(JSON, default=dict)  # {session_id, start, end, text}
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class EditComparison(Base):
    """Phase 6B: a person compared two edits of the same clip (optionally blind)."""

    __tablename__ = "edit_comparisons"
    id: Mapped[int] = mapped_column(primary_key=True)
    clip_id: Mapped[int] = mapped_column(ForeignKey("clips.id", ondelete="CASCADE"), index=True)
    left: Mapped[str] = mapped_column(String(16))
    right: Mapped[str] = mapped_column(String(16))
    left_structure: Mapped[str] = mapped_column(String(8))
    right_structure: Mapped[str] = mapped_column(String(8))
    winner: Mapped[str] = mapped_column(String(8))  # left | right | equal
    blind: Mapped[bool] = mapped_column(Boolean, default=True)
    reviewer: Mapped[str] = mapped_column(String(255), default="")
    note: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


# ---------------------------------------------------------------------------
# Multi-user (HTS SaaS, stage 1): clips the admin shares with specific accounts
# ---------------------------------------------------------------------------


class ClipShare(Base):
    """One clip made available to one account. History is kept: revoking sets ``revoked_at``
    (at most ONE active share per clip+user, enforced by a partial unique index)."""

    __tablename__ = "clip_shares"
    __table_args__ = (
        Index("uq_clip_shares_active", "clip_id", "user_id", unique=True,
              postgresql_where=text("revoked_at IS NULL"), sqlite_where=text("revoked_at IS NULL")),
        Index("ix_clip_shares_user_active", "user_id", "revoked_at"),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    clip_id: Mapped[int] = mapped_column(ForeignKey("clips.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    can_view: Mapped[bool] = mapped_column(Boolean, default=True)
    can_download: Mapped[bool] = mapped_column(Boolean, default=False)
    can_variants: Mapped[bool] = mapped_column(Boolean, default=False)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    revoked_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    # immutable media snapshot: the exact file the admin approved for this account. A later re-edit,
    # re-render or promoted variant never changes what is shared (unless follow_latest is chosen).
    video_path: Mapped[str] = mapped_column(String(512), default="")
    video_sha256: Mapped[str] = mapped_column(String(64), default="")
    video_bytes: Mapped[int] = mapped_column(Integer, default=0)
    thumb_path: Mapped[str] = mapped_column(String(512), default="")
    clip_version: Mapped[int] = mapped_column(Integer, default=0)
    variants: Mapped[dict] = mapped_column(JSON, default=dict)  # {name: {video_path, sha256, label}}
    follow_latest: Mapped[bool] = mapped_column(Boolean, default=False)
    grant_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)  # created by a StreamerGrant


class StreamerGrant(Base):
    """An account sees every APPROVED clip of a streamer: the current ones and the ones approved later.
    Materialised as ordinary clip shares tagged with ``grant_id`` (same snapshot/protection/authorization)."""

    __tablename__ = "streamer_grants"
    __table_args__ = (
        Index("uq_streamer_grants_active", "user_id", "streamer_id", unique=True,
              postgresql_where=text("revoked_at IS NULL"), sqlite_where=text("revoked_at IS NULL")),
        Index("ix_streamer_grants_user", "user_id", "revoked_at"),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    streamer_id: Mapped[int] = mapped_column(ForeignKey("streamers.id", ondelete="CASCADE"))
    can_download: Mapped[bool] = mapped_column(Boolean, default=False)
    # also clips still waiting for review (rendered, status "ready"), not only the approved ones
    include_pending: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("false"))
    expires_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    revoked_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)


# ---------------------------------------------------------------------------
# Phase 6C - references lab and visual format profiles
# ---------------------------------------------------------------------------


class RefItem(Base):
    """A reference channel/profile/video registered by the owner. API data (``api_data``) is kept
    apart from the owner's own notes and expires (YouTube API policy: refresh or delete <= 30 days)."""

    __tablename__ = "ref_items"
    id: Mapped[int] = mapped_column(primary_key=True)
    platform: Mapped[str] = mapped_column(String(16))  # youtube | instagram | other
    kind: Mapped[str] = mapped_column(String(16))  # channel | video | short | profile | reel | post | image
    url: Mapped[str] = mapped_column(String(1024), default="")
    external_id: Mapped[str] = mapped_column(String(128), default="", index=True)
    handle: Mapped[str] = mapped_column(String(128), default="")
    title: Mapped[str] = mapped_column(String(300), default="")
    category: Mapped[str] = mapped_column(String(64), default="")
    games: Mapped[list] = mapped_column(JSON, default=list)
    humor: Mapped[list] = mapped_column(JSON, default=list)
    formats: Mapped[list] = mapped_column(JSON, default=list)
    tags: Mapped[list] = mapped_column(JSON, default=list)
    why: Mapped[str] = mapped_column(Text, default="")
    notes: Mapped[str] = mapped_column(Text, default="")
    favorite: Mapped[bool] = mapped_column(Boolean, default=False)
    image_path: Mapped[str] = mapped_column(String(512), default="")
    api_data: Mapped[dict] = mapped_column(JSON, default=dict)
    api_source: Mapped[str] = mapped_column(String(64), default="")  # e.g. youtube-data-api-v3
    api_fetched_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    api_expires_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, index=True)
    created_by: Mapped[str] = mapped_column(String(255), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class RefCollection(Base):
    __tablename__ = "ref_collections"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    description: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class RefCollectionItem(Base):
    __tablename__ = "ref_collection_items"
    collection_id: Mapped[int] = mapped_column(ForeignKey("ref_collections.id", ondelete="CASCADE"), primary_key=True)
    item_id: Mapped[int] = mapped_column(ForeignKey("ref_items.id", ondelete="CASCADE"), primary_key=True)


class FormatProfile(Base):
    """Abstract visual format (layout, webcam, captions, badge...) extracted from a screenshot or
    written by hand. Never stores third-party logos/graphics: only parameters."""

    __tablename__ = "format_profiles"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    item_id: Mapped[int | None] = mapped_column(ForeignKey("ref_items.id", ondelete="SET NULL"), nullable=True)
    image_path: Mapped[str] = mapped_column(String(512), default="")  # the uploaded print (owner's upload)
    avatar_path: Mapped[str] = mapped_column(String(512), default="")  # OUR avatar for the badge (owner's upload)
    status: Mapped[str] = mapped_column(String(16), default="draft")  # draft | queued | ready | failed
    analysis: Mapped[dict] = mapped_column(JSON, default=dict)
    params: Mapped[dict] = mapped_column(JSON, default=dict)
    error: Mapped[str] = mapped_column(Text, default="")
    favorite: Mapped[bool] = mapped_column(Boolean, default=False)
    created_by: Mapped[str] = mapped_column(String(255), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)
