"""Model -> JSON helpers for the public API."""

from __future__ import annotations

from datetime import datetime

from ..models import Clip, Job, Streamer, StreamSession, Worker


def iso(dt: datetime | None) -> str | None:
    return dt.isoformat() + "Z" if dt else None


def streamer(s: Streamer, live_session: StreamSession | None = None, clip_count: int = 0) -> dict:
    return {
        "id": s.id,
        "platform": s.platform,
        "channel": s.channel,
        "url": s.url,
        "display_name": s.display_name or s.channel,
        "enabled": s.enabled,
        "status": s.status,
        "stream_title": s.stream_title,
        "preferences": s.preferences or {},
        "last_checked_at": iso(s.last_checked_at),
        "last_live_at": iso(s.last_live_at),
        "last_error": s.last_error,
        "created_at": iso(s.created_at),
        "live_session": session(live_session) if live_session else None,
        "clip_count": clip_count,
    }


def session(s: StreamSession) -> dict:
    return {
        "id": s.id,
        "streamer_id": s.streamer_id,
        "status": s.status,
        "title": s.title,
        "started_at": iso(s.started_at),
        "ended_at": iso(s.ended_at),
        "end_reason": s.end_reason,
        "capture_worker_id": s.capture_worker_id,
        "covered_until": round(s.covered_until, 2),
        "analyzed_until": round(s.analyzed_until, 2),
        "scored_until": round(s.scored_until, 2),
        "segment_count": s.segment_count,
    }


def clip(c: Clip, streamer_obj: Streamer | None = None) -> dict:
    return {
        "id": c.id,
        "session_id": c.session_id,
        "streamer_id": c.streamer_id,
        "streamer": {"display_name": (streamer_obj.display_name or streamer_obj.channel), "platform":
                     streamer_obj.platform, "channel": streamer_obj.channel} if streamer_obj else None,
        "status": c.status,
        "start": round(c.start, 2),
        "end": round(c.end, 2),
        "peak": round(c.peak, 2),
        "duration": round(c.duration, 2),
        "score": round(c.score, 1),
        "score_breakdown": c.score_breakdown or {},
        "reasons": c.reasons or [],
        "kinds": c.kinds or [],
        "transcript": c.transcript,
        "title": c.title,
        "description": c.description,
        "title_suggestions": c.title_suggestions or [],
        "hashtags": c.hashtags or [],
        "caption_style": c.caption_style,
        "layout": c.layout,
        "webcam_region": c.webcam_region,
        "vision": c.vision,
        "vision_state": c.vision_state,
        "analysis": c.analysis,
        "decision": c.decision,
        "edit_plan": c.edit_plan,
        "analysis_meta": c.analysis_meta,
        "analysis_runs": c.analysis_runs,
        "is_test": bool(c.is_test),
        "prelim_score": c.prelim_score, "editorial_score": c.editorial_score, "confidence": c.confidence,
        "situation_id": c.situation_id, "protected": bool(c.protected), "favorite": bool(c.favorite),
        "lifecycle": c.lifecycle, "purge_after": iso(c.purge_after), "purge_reason": c.purge_reason,
        "heuristic_bounds": [c.heuristic_start, c.heuristic_end] if c.heuristic_start is not None else None,
        "version": c.version,
        "gate": c.gate or "",
        "gate_detail": (c.decision or {}).get("gate"),
        "has_video": bool(c.video_path),
        "has_thumbnail": bool(c.thumb_path),
        "file_size": c.file_size,
        "sha256": c.sha256,
        "render_seconds": round(c.render_seconds, 2),
        "error": c.error,
        "created_at": iso(c.created_at),
        "updated_at": iso(c.updated_at),
    }


def job(j: Job) -> dict:
    return {
        "id": j.id,
        "type": j.type,
        "status": j.status,
        "attempts": j.attempts,
        "max_attempts": j.max_attempts,
        "requires_gpu": j.requires_gpu,
        "worker_id": j.worker_id,
        "last_error": j.last_error[-600:],
        "payload": {k: v for k, v in (j.payload or {}).items() if not k.startswith("_")},
        "created_at": iso(j.created_at),
        "started_at": iso(j.started_at),
        "finished_at": iso(j.finished_at),
    }


def worker(w: Worker) -> dict:
    return {
        "id": w.id,
        "host": w.host,
        "host_label": w.host_label,
        "roles": w.roles or [],
        "capabilities": w.capabilities or {},
        "base_url": w.base_url,
        "version": w.version,
        "status": w.status,
        "metrics": w.metrics or {},
        "started_at": iso(w.started_at),
        "last_heartbeat_at": iso(w.last_heartbeat_at),
    }
