"""Pipeline diagnostics and TESTE MANUAL clips."""

from __future__ import annotations

from datetime import timedelta

from sqlalchemy import select

from clipper import queue
from clipper.db import session_scope, utcnow
from clipper.models import Clip, Job, Segment, Streamer, StreamSession, TranscriptWord, Worker
from clipper.scoring.engine import score_timeline
from conftest import CSRF


def _session(tag: str, *, live=True, segments=6, stats=None) -> tuple[int, int]:
    with session_scope() as db:
        s = Streamer(platform="twitch", channel=f"dg_{tag}", url=f"https://www.twitch.tv/dg_{tag}",
                     display_name=f"Diag {tag}", status="live" if live else "offline")
        db.add(s)
        db.flush()
        sess = StreamSession(streamer_id=s.id, status="live" if live else "ended", stats=stats or {},
                             covered_until=segments * 10.0, analyzed_until=segments * 10.0)
        db.add(sess)
        db.flush()
        for i in range(segments):
            db.add(Segment(session_id=sess.id, idx=i, start=i * 10.0, duration=10.0, analysis_state="done",
                           captured_at=utcnow() - timedelta(seconds=(segments - i) * 10)))
        for t, w in ((12.0, "olha"), (25.0, "isso"), (51.0, "caramba")):
            db.add(TranscriptWord(session_id=sess.id, segment_idx=int(t // 10), start=t, end=t + 0.3, word=w))
        return s.id, sess.id


def _workers(*roles):
    with session_scope() as db:
        for r in roles:
            w = db.get(Worker, f"diag-{r}") or Worker(id=f"diag-{r}")
            w.roles, w.status, w.last_heartbeat_at = [r], "online", utcnow()
            db.merge(w)


def _report_for(admin, sid):
    rep = admin.get("/api/diagnostics").json()
    return next(x for x in rep["streamers"] if x["streamer"]["id"] == sid)


def test_engine_reports_rejected_moments_with_reason():
    import json

    from conftest import FIXTURES

    tl = json.loads((FIXTURES / "timeline_event.json").read_text())
    tl["settings"]["min_score"] = 99.0
    res = score_timeline(tl)
    assert res["candidates"] == []
    assert any(r["reason"] == "below_min_score" and r["score"] > 50 for r in res["rejected"])


def test_diagnostics_explains_missing_scoring_worker(admin):
    sid, sess_id = _session("noscore")
    _workers("capture", "audio", "render")
    with session_scope() as db:
        for k in range(3):
            queue.enqueue(db, "score", {"session_id": sess_id}, idempotency_key=f"dg-score-{sess_id}-{k}")
        for w in db.scalars(select(Worker).where(Worker.id.like("%scoring%"))):
            w.last_heartbeat_at = utcnow() - timedelta(hours=1)
    r = _report_for(admin, sid)
    assert r["state"] == "error" and "pontuação indisponível" in r["explanation"]
    assert r["buffer"]["seconds"] == 60.0 and r["last_segment"]["idx"] == 5
    with session_scope() as db:  # leave the queue clean for other tests
        for j in db.scalars(select(Job).where(Job.idempotency_key.like(f"dg-score-{sess_id}-%"))):
            j.status = "cancelled"


def test_diagnostics_explains_no_clip_with_min_score(admin):
    stats = {"last_scored_at": "2026-11-18T05:00:00Z", "rejected_counts": {"below_min_score": 3, "text_only": 9},
             "best_rejected_score": 48.2}
    sid, _ = _session("below", stats=stats)
    _workers("capture", "audio", "scoring", "render")
    r = _report_for(admin, sid)
    assert r["state"] == "ok"
    assert "Nenhum candidato atual atingiu a nota mínima de 55" in r["explanation"]
    assert "48.2" in r["explanation"] and "3 abaixo da nota mínima" in r["explanation"]


def test_manual_test_clip_lifecycle(admin):
    sid, sess_id = _session("manual")
    r = admin.post(f"/api/streamers/{sid}/test-clip", json={"mode": "last30"}, headers=CSRF)
    assert r.status_code == 201, r.text
    c = r.json()
    assert c["is_test"] and c["title"].startswith("TESTE MANUAL")
    assert (c["start"], c["end"]) == (30.0, 60.0) and c["status"] in ("analyzing", "rendering")
    assert "caramba" in c["transcript"]
    again = admin.post(f"/api/streamers/{sid}/test-clip", json={"mode": "last30"}, headers=CSRF).json()
    assert again["id"] == c["id"]  # idempotent: no duplicate job/clip on double click
    bad = admin.post(f"/api/streamers/{sid}/test-clip", json={"mode": "custom", "start": 50, "end": 500}, headers=CSRF)
    assert bad.status_code == 422 and "fora do buffer" in bad.json()["detail"]
    path = admin.post(f"/api/streamers/{sid}/test-clip", json={"mode": "file:///etc/passwd"}, headers=CSRF)
    assert path.status_code == 422
    # the director can never discard or move a test clip
    from clipper import pipeline

    with session_scope() as db:
        pipeline.post_vision(db, c["id"], None, "sem worker")
    after = admin.get(f"/api/clips/{c['id']}").json()
    assert after["status"] == "rendering" and (after["start"], after["end"]) == (30.0, 60.0)
    # tests are excluded from quality metrics
    stats = admin.get("/api/stats").json()
    with session_scope() as db:
        real = db.scalar(select(Clip).where(Clip.is_test.is_(False)).limit(1)) is not None
        n_real = len(db.scalars(select(Clip).where(Clip.is_test.is_(False))).all())
    assert stats["clips_total"] == n_real or not real


def test_manual_test_clip_concurrency_limit_and_auth(admin, client):
    sid, _ = _session("limit", segments=12)
    ok = [admin.post(f"/api/streamers/{sid}/test-clip", json={"mode": "custom", "start": s, "end": s + 10},
                     headers=CSRF) for s in (0, 20, 40, 60)]
    codes = [r.status_code for r in ok]
    assert 429 in codes  # at most MAX_TESTS_IN_FLIGHT in progress
    client.cookies.clear()
    assert client.post(f"/api/streamers/{sid}/test-clip", json={"mode": "last30"}).status_code == 401
    with session_scope() as db:  # finish them so other tests are not throttled
        for c in db.scalars(select(Clip).where(Clip.is_test.is_(True))):
            c.status = "ready"


def test_retry_failed_requeues_dead_jobs_once(admin):
    sid, sess_id = _session("retry")
    with session_scope() as db:
        j = queue.enqueue(db, "score", {"session_id": sess_id}, idempotency_key=f"dg-dead-{sess_id}")
        j.status, j.attempts, j.last_error = "dead", 3, "boom"
    r = admin.post(f"/api/diagnostics/streamers/{sid}/retry-failed", headers=CSRF).json()
    assert len(r["retried"]) == 1
    assert admin.post(f"/api/diagnostics/streamers/{sid}/retry-failed", headers=CSRF).json()["retried"] == []
    with session_scope() as db:
        job = db.scalar(select(Job).where(Job.idempotency_key == f"dg-dead-{sess_id}"))
        assert job.status == "queued" and job.attempts == 0
        job.status = "cancelled"
