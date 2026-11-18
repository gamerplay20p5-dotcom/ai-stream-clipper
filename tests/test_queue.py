from datetime import timedelta

from clipper import queue
from clipper.db import utcnow
from clipper.models import Job


def _fresh(db, prefix):
    # isolate from other tests sharing the DB
    for j in db.query(Job).filter(Job.status.in_(("queued", "running"))):
        j.status = "cancelled"
    db.flush()


def test_enqueue_is_idempotent(db):
    _fresh(db, "idem")
    a = queue.enqueue(db, "render", {"clip_id": 1}, idempotency_key="t-idem-1")
    b = queue.enqueue(db, "render", {"clip_id": 999}, idempotency_key="t-idem-1")
    assert a.id == b.id and b.payload == {"clip_id": 1}


def test_claim_respects_gpu_and_type(db):
    _fresh(db, "gpu")
    gpu_job = queue.enqueue(db, "vision_analyze", {"clip_id": 1}, requires_gpu=True, idempotency_key="t-gpu-1")
    assert queue.claim(db, "cpu-worker", ["vision_analyze"], has_gpu=False) == []
    assert queue.claim(db, "cpu-worker", ["render"], has_gpu=True) == []
    got = queue.claim(db, "gpu-worker", ["vision_analyze"], has_gpu=True)
    assert [j.id for j in got] == [gpu_job.id]
    assert got[0].status == "running" and got[0].attempts == 1 and got[0].lease_expires_at is not None
    # already claimed -> nobody else gets it
    assert queue.claim(db, "gpu-worker-2", ["vision_analyze"], has_gpu=True) == []


def test_retry_backoff_then_dead_letter(db):
    _fresh(db, "dlq")
    j = queue.enqueue(db, "render", {"clip_id": 2}, idempotency_key="t-dlq-1", max_attempts=2)
    queue.claim(db, "w", ["render"], has_gpu=False)
    assert queue.fail(db, j.id, "w", "boom") == "queued"
    assert j.run_after > utcnow()  # exponential backoff
    assert queue.claim(db, "w", ["render"], has_gpu=False) == []  # not yet runnable
    j.run_after = utcnow() - timedelta(seconds=1)
    queue.claim(db, "w", ["render"], has_gpu=False)
    assert queue.fail(db, j.id, "w", "boom again") == "dead"
    assert j.status == "dead" and "boom again" in j.last_error
    assert queue.retry(db, j.id).status == "queued"


def test_non_retryable_failure_goes_straight_to_dead(db):
    _fresh(db, "nr")
    j = queue.enqueue(db, "render", {"clip_id": 3}, idempotency_key="t-nr-1", max_attempts=5)
    queue.claim(db, "w", ["render"], has_gpu=False)
    assert queue.fail(db, j.id, "w", "bad input", retryable=False) == "dead"


def test_lease_expiry_requeues_lost_jobs(db):
    """Simulates a worker crash: the lease expires and the job is re-queued."""
    _fresh(db, "lease")
    j = queue.enqueue(db, "render", {"clip_id": 4}, idempotency_key="t-lease-1")
    queue.claim(db, "crashy", ["render"], has_gpu=False, lease_seconds=60)
    ok, _ = queue.renew(db, j.id, "crashy")
    assert ok
    assert queue.renew(db, j.id, "impostor") == (False, True)
    assert not queue.complete(db, j.id, "impostor")
    j.lease_expires_at = utcnow() - timedelta(seconds=1)
    expired = queue.expire_leases(db)
    assert [x.id for x in expired] == [j.id] and j.status == "queued" and j.worker_id == ""


def test_global_concurrency_limit(db):
    _fresh(db, "limit")
    queue.enqueue(db, "render", {"clip_id": 5}, idempotency_key="t-lim-1")
    queue.enqueue(db, "render", {"clip_id": 6}, idempotency_key="t-lim-2")
    assert len(queue.claim(db, "w", ["render"], has_gpu=False, global_limits={"render": 1})) == 1
    assert queue.claim(db, "w2", ["render"], has_gpu=False, global_limits={"render": 1}) == []


def test_cancel(db):
    _fresh(db, "cancel")
    j = queue.enqueue(db, "render", {"clip_id": 7}, idempotency_key="t-cancel-1")
    queue.request_cancel(db, j)
    assert j.status == "cancelled"
    k = queue.enqueue(db, "render", {"clip_id": 8}, idempotency_key="t-cancel-2")
    queue.claim(db, "w", ["render"], has_gpu=False)
    queue.request_cancel(db, k)
    assert queue.renew(db, k.id, "w") == (True, True)
