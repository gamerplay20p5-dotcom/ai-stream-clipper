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
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base, utcnow


class User(Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255), default="")
    role: Mapped[str] = mapped_column(String(20), default="admin")  # admin | viewer
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


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
