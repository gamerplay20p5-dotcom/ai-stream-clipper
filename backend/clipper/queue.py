"""Durable job queue on top of the relational database.

Design:
* Jobs are rows; state changes are transactional, so they survive restarts of any
  component (API, workers, database) without corrupting state.
* ``idempotency_key`` is UNIQUE - enqueueing the same logical work twice returns the
  existing job.
* Claiming uses ``SELECT ... FOR UPDATE SKIP LOCKED`` on PostgreSQL plus a conditional
  ``UPDATE ... WHERE status='queued'`` (also correct on SQLite, which serialises writers).
* A claimed job holds a *lease*; workers renew it with heartbeats. Expired leases are
  re-queued (worker crash) with exponential backoff until ``max_attempts`` is reached,
  after which the job is moved to the ``dead`` state (dead-letter queue).
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import timedelta

from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .db import utcnow
from .models import Job

log = logging.getLogger(__name__)

# job type -> worker role able to execute it
JOB_ROLES: dict[str, str] = {
    "capture": "capture",
    "audio_analyze": "audio",
    "score": "scoring",
    "vision_analyze": "vision",
    "render": "render",
    "dataset_media": "render",  # Phase 3: extract + re-encode media of evaluation examples (206)
}

TERMINAL = {"done", "dead", "cancelled"}


def backoff_seconds(attempts: int) -> int:
    return int(min(300, 5 * (2 ** max(0, attempts - 1))))


def enqueue(
    db: Session,
    type_: str,
    payload: dict,
    *,
    idempotency_key: str | None = None,
    priority: int = 100,
    requires_gpu: bool = False,
    max_attempts: int = 4,
    delay_s: float = 0.0,
) -> Job:
    if type_ not in JOB_ROLES:
        raise ValueError(f"unknown job type {type_}")
    if idempotency_key:
        existing = db.scalar(select(Job).where(Job.idempotency_key == idempotency_key))
        if existing is not None:
            return existing
    job = Job(
        type=type_,
        payload=payload,
        idempotency_key=idempotency_key,
        priority=priority,
        requires_gpu=requires_gpu,
        max_attempts=max_attempts,
        run_after=utcnow() + timedelta(seconds=delay_s),
    )
    try:
        with db.begin_nested():
            db.add(job)
            db.flush()
    except IntegrityError:
        existing = db.scalar(select(Job).where(Job.idempotency_key == idempotency_key))
        if existing is None:
            raise
        return existing
    log.info("job enqueued", extra={"job_id": job.id, "job_type": type_, "key": idempotency_key})
    return job


def claim(
    db: Session,
    worker_id: str,
    types: list[str],
    *,
    has_gpu: bool,
    limit: int = 1,
    lease_seconds: int = 90,
    global_limits: dict[str, int] | None = None,
    accept: Callable[[Job], bool] | None = None,
) -> list[Job]:
    """Atomically claim up to ``limit`` runnable jobs of the given types."""
    if not types or limit <= 0:
        return []
    db.flush()  # sessions use autoflush=False; make pending changes visible to the query
    now = utcnow()
    allowed = list(types)
    if global_limits:
        running = dict(
            db.execute(
                select(Job.type, func.count()).where(Job.status == "running", Job.type.in_(allowed)).group_by(Job.type)
            ).all()
        )
        allowed = [t for t in allowed if running.get(t, 0) < global_limits.get(t, 1 << 30)]
        if not allowed:
            return []
    q = select(Job).where(Job.status == "queued", Job.type.in_(allowed), Job.run_after <= now)
    if not has_gpu:
        q = q.where(Job.requires_gpu.is_(False))
    q = q.order_by(Job.priority, Job.id).limit(limit * 4)
    if db.get_bind().dialect.name == "postgresql":
        q = q.with_for_update(skip_locked=True)
    candidates = list(db.scalars(q))
    ids = [j.id for j in candidates if accept is None or accept(j)]
    claimed: list[Job] = []
    for jid in ids:
        if len(claimed) >= limit:
            break
        res = db.execute(
            update(Job)
            .where(Job.id == jid, Job.status == "queued")
            .values(
                status="running",
                worker_id=worker_id,
                attempts=Job.attempts + 1,
                started_at=now,
                lease_expires_at=now + timedelta(seconds=lease_seconds),
            )
        )
        if res.rowcount == 1:
            claimed.append(db.get(Job, jid))
    db.flush()
    for j in claimed:
        db.refresh(j)
    return claimed


def _owned(db: Session, job_id: int, worker_id: str) -> Job | None:
    job = db.get(Job, job_id)
    if job is None or job.status != "running" or job.worker_id != worker_id:
        return None
    return job


def renew(db: Session, job_id: int, worker_id: str, lease_seconds: int = 90) -> tuple[bool, bool]:
    """Extend a lease. Returns ``(still_owned, cancel_requested)``."""
    job = db.get(Job, job_id)
    if job is None:
        return False, True
    if job.status == "cancelled":
        return False, True
    if job.status != "running" or job.worker_id != worker_id:
        return False, True
    job.lease_expires_at = utcnow() + timedelta(seconds=lease_seconds)
    return True, bool((job.payload or {}).get("_cancel"))


def complete(db: Session, job_id: int, worker_id: str, result: dict | None = None) -> bool:
    job = _owned(db, job_id, worker_id)
    if job is None:
        return False
    job.status = "done"
    job.result = result or {}
    job.finished_at = utcnow()
    job.lease_expires_at = None
    log.info("job done", extra={"job_id": job.id, "job_type": job.type})
    return True


def fail(db: Session, job_id: int, worker_id: str, error: str, *, retryable: bool = True) -> str | None:
    """Record a failure. Returns the new status (``queued`` or ``dead``)."""
    job = _owned(db, job_id, worker_id)
    if job is None:
        return None
    _requeue_or_bury(job, error, retryable)
    return job.status


def _requeue_or_bury(job: Job, error: str, retryable: bool) -> None:
    job.last_error = (error or "")[-4000:]
    job.lease_expires_at = None
    if retryable and job.attempts < job.max_attempts:
        job.status = "queued"
        job.worker_id = ""
        job.run_after = utcnow() + timedelta(seconds=backoff_seconds(job.attempts))
        log.warning("job failed, retrying", extra={"job_id": job.id, "job_type": job.type, "attempts": job.attempts})
    else:
        job.status = "dead"
        job.finished_at = utcnow()
        log.error("job moved to dead-letter", extra={"job_id": job.id, "job_type": job.type, "error": error[:300]})


def expire_leases(db: Session) -> list[Job]:
    db.flush()
    now = utcnow()
    expired = list(db.scalars(select(Job).where(Job.status == "running", Job.lease_expires_at < now)))
    for job in expired:
        _requeue_or_bury(job, f"lease expired (worker {job.worker_id} lost)", retryable=True)
    return expired


def retry(db: Session, job_id: int) -> Job | None:
    job = db.get(Job, job_id)
    if job is None or job.status not in ("dead", "cancelled", "done"):
        return None
    job.status = "queued"
    job.attempts = 0
    job.run_after = utcnow()
    job.worker_id = ""
    job.finished_at = None
    return job


def request_cancel(db: Session, job: Job) -> None:
    if job.status == "queued":
        job.status = "cancelled"
        job.finished_at = utcnow()
    elif job.status == "running":
        job.payload = {**(job.payload or {}), "_cancel": True}


def queue_stats(db: Session) -> dict[str, dict[str, int]]:
    rows = db.execute(select(Job.type, Job.status, func.count()).group_by(Job.type, Job.status)).all()
    out: dict[str, dict[str, int]] = {}
    for t, s, n in rows:
        out.setdefault(t, {})[s] = n
    return out
