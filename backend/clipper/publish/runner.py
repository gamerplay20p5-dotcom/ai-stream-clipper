"""The publisher service (``clipper publisher``), run as its own container on 210.

It talks to PostgreSQL directly (like the API) so OAuth tokens never travel over the internal
worker API, reads rendered videos from the API storage volume (read-only), claims ``publish``
jobs from the shared queue and talks to the platforms over HTTPS.

Loop: heartbeat -> automatic rules -> queue due publications -> upload one -> poll processing
-> refresh official metrics. Retries use the publication's own schedule (exponential backoff or
the platform's Retry-After); a restart resumes the saved upload session instead of re-uploading.
"""

from __future__ import annotations

import logging
import signal
import socket
import threading
import time
from datetime import timedelta
from types import SimpleNamespace

from sqlalchemy import select

from .. import __version__, queue, storage
from ..config import get_settings
from ..db import init_engine, session_scope, utcnow
from ..models import Clip, Job, Publication, PublishAccount, PublishRule, Worker
from ..security import sha256_file
from . import service
from .base import PublishError

log = logging.getLogger(__name__)

MAX_ATTEMPTS = 6
POLL_EVERY_S = 60
METRICS_EVERY_S = 6 * 3600
SCAN_EVERY_S = 60
MIN_GAP_BETWEEN_UPLOADS_S = 60  # per account: never burst uploads


def worker_id() -> str:
    s = get_settings()
    return s.worker_id or f"{s.worker_host_label or socket.gethostname()}-publisher"


def register(db) -> None:
    wid = worker_id()
    w = db.get(Worker, wid) or Worker(id=wid)
    now = utcnow()
    w.host, w.host_label, w.roles = socket.gethostname(), get_settings().worker_host_label, ["publisher"]
    w.capabilities = {"platforms": sorted(service.PROVIDERS)}
    w.version, w.status, w.started_at, w.last_heartbeat_at = __version__, "online", now, now
    db.merge(w)
    # jobs held by a previous incarnation go back to the queue (the upload itself resumes)
    for j in db.scalars(select(Job).where(Job.status == "running", Job.worker_id == wid)):
        queue._requeue_or_bury(j, "publisher restarted", retryable=True)


def heartbeat(db) -> None:
    w = db.get(Worker, worker_id())
    if w is not None:
        w.last_heartbeat_at, w.status = utcnow(), "online"


def _snapshot(pub: Publication) -> SimpleNamespace:
    return SimpleNamespace(id=pub.id, title=pub.title, description=pub.description, tags=list(pub.tags or []),
                           privacy=pub.privacy, upload_state=dict(pub.upload_state or {}), remote_id=pub.remote_id,
                           remote_status=dict(pub.remote_status or {}))


def _quota_day() -> str:
    from zoneinfo import ZoneInfo

    from . import youtube

    return utcnow().replace(tzinfo=ZoneInfo("UTC")).astimezone(ZoneInfo(youtube.QUOTA_TZ)).strftime("%Y-%m-%d")


def _quota(acct: PublishAccount) -> dict:
    """Today's usage. Two buckets since June 2026: uploads (videos.insert calls) and other units."""
    q = dict(acct.quota or {})
    if q.get("day") != _quota_day() or "uploads" not in q:  # new day, or pre-0.4.5 format
        q = {"day": _quota_day(), "uploads": 0, "units": 0}
    return q


def _youtube_quota_ok(acct: PublishAccount) -> bool:
    from . import youtube

    limit = int((acct.meta or {}).get("upload_limit", youtube.UPLOAD_DAILY_LIMIT))
    return _quota(acct)["uploads"] < limit


def _add_quota(acct: PublishAccount, *, uploads: int = 0, units: int = 0) -> None:
    q = _quota(acct)
    q["uploads"] += uploads
    q["units"] += units
    acct.quota = q


def _reschedule(pub: Publication, err: PublishError | str, retry_after: float | None = None) -> None:
    msg = str(err)
    pub.last_error = msg[:2000]
    if pub.attempts >= MAX_ATTEMPTS:
        pub.status = "failed"
        pub.remote_status = {**(pub.remote_status or {}), "failure": "before_publish"}
        service.log_event(pub, "failed", f"desistiu após {pub.attempts} tentativas: {msg}")
        return
    wait = retry_after or min(6 * 3600, 60 * 2 ** max(0, pub.attempts - 1))
    pub.status = "scheduled"
    pub.scheduled_at = utcnow() + timedelta(seconds=wait)
    service.log_event(pub, "retry", f"nova tentativa em {int(wait)} s: {msg}")


def media_for(pub: Publication, clip: Clip) -> tuple[str | None, str]:
    """(relative path, expected sha) of the media this publication must send, or (None, reason)."""
    if pub.video_path:  # snapshot taken at creation (v0.6.1+)
        return pub.video_path, pub.video_sha256 or ""
    # older publications: only the clip's current file, and only if it is still the scheduled one
    if pub.video_sha256 and clip.sha256 and pub.video_sha256 != clip.sha256:
        return None, ("o vídeo do corte mudou depois que esta publicação foi criada; crie a publicação de novo "
                      "para enviar a versão atual")
    return clip.video_path, clip.sha256 or ""


def run_publish(job_id: int, pub_id: int) -> None:
    wid = worker_id()
    with session_scope() as db:
        pub = db.get(Publication, pub_id)
        if pub is None or pub.status in ("published", "cancelled", "failed", "processing"):
            queue.complete(db, job_id, wid, {"skipped": pub.status if pub else "gone"})
            return
        acct = db.get(PublishAccount, pub.account_id)
        clip = db.get(Clip, pub.clip_id) if pub.clip_id else None
        rule = db.get(PublishRule, pub.rule_id) if pub.rule_id else None
        problems = ["o corte foi excluído"] if clip is None else service.eligibility(
            db, clip, acct, auto=pub.origin in ("auto", "semi"), rule=rule, privacy=pub.privacy, rights_confirmed=True,
            semi=pub.origin == "semi")
        if problems:  # conditions changed since scheduling (rejected, reviewed as bad, account revoked...)
            pub.status, pub.last_error = "failed", "; ".join(problems)
            pub.remote_status = {**(pub.remote_status or {}), "failure": "before_publish"}
            service.log_event(pub, "refused", pub.last_error)
            queue.complete(db, job_id, wid, {"refused": problems})
            return
        last = db.scalar(select(Publication.updated_at).where(Publication.account_id == acct.id,
                                                              Publication.status.in_(("processing", "published")),
                                                              Publication.id != pub.id)
                         .order_by(Publication.updated_at.desc()))
        if last and (utcnow() - last).total_seconds() < MIN_GAP_BETWEEN_UPLOADS_S:
            _reschedule(pub, "intervalo mínimo entre envios", MIN_GAP_BETWEEN_UPLOADS_S)
            queue.complete(db, job_id, wid, {"deferred": "rate"})
            return
        if acct.platform == "youtube" and not _youtube_quota_ok(acct):
            _reschedule(pub, "limite diário de envios da YouTube Data API atingido (bucket Video Uploads)", 3 * 3600)
            queue.complete(db, job_id, wid, {"deferred": "quota"})
            return
        try:
            tokens = service.tokens_for(db, acct)
        except PublishError as exc:
            if exc.auth:
                pub.status, pub.last_error = "failed", str(exc)
                service.log_event(pub, "auth", str(exc))
            else:
                _reschedule(pub, exc, exc.retry_after)
            queue.complete(db, job_id, wid, {"error": str(exc)[:300]})
            return
        # the publication sends EXACTLY the media approved when it was created (snapshot), never
        # "whatever the clip shows now": a re-render or a promoted variant must not change it silently
        rel, want = media_for(pub, clip)
        if rel is None:
            pub.status, pub.last_error = "failed", want
            service.log_event(pub, "refused", want)
            queue.complete(db, job_id, wid, {"error": "media changed"})
            return
        path = storage.resolve(rel)
        if not path.is_file():
            pub.status, pub.last_error = "failed", "arquivo do vídeo não encontrado no armazenamento"
            queue.complete(db, job_id, wid, {"error": "missing file"})
            return
        if want and sha256_file(path) != want:
            pub.status, pub.last_error = "failed", "o arquivo do vídeo não confere com o SHA-256 registrado"
            queue.complete(db, job_id, wid, {"error": "sha mismatch"})
            return
        pub.status = "uploading"
        pub.attempts += 1
        service.log_event(pub, "upload", f"tentativa {pub.attempts}" + (" (retomando sessão)" if pub.upload_state else ""))
        snap, platform, acct_id = _snapshot(pub), acct.platform, acct.id

    def save_state(st: dict) -> None:  # committed immediately: a crash right after still knows the session
        with session_scope() as s2:
            p = s2.get(Publication, pub_id)
            p.upload_state = st

    prov = service.provider(platform)
    try:
        res = prov.upload(snap, tokens, path, save_state)
        err = None
    except PublishError as exc:
        res, err = None, exc
    except Exception as exc:  # network etc.: retry with backoff
        log.exception("upload crashed", extra={"publication": pub_id})
        res, err = None, PublishError(f"{type(exc).__name__}: {exc}", retryable=True)
    with session_scope() as db:
        pub = db.get(Publication, pub_id)
        acct = db.get(PublishAccount, acct_id)
        if res is not None:
            pub.remote_id, pub.remote_url = res.remote_id, res.remote_url or pub.remote_url
            pub.remote_status = res.remote_status
            pub.status = "published" if res.state == "published" else "processing"
            pub.last_error = ""
            if pub.status == "published":
                pub.published_at = utcnow()
            if platform == "youtube":
                _add_quota(acct, uploads=1)
            service.log_event(pub, "uploaded", f"id remoto {res.remote_id}")
            queue.complete(db, job_id, wid, {"remote_id": res.remote_id})
            return
        if err.auth:
            acct.status, acct.last_error = "expired", str(err)
            pub.status, pub.last_error = "failed", str(err)
            pub.remote_status = {**(pub.remote_status or {}), "failure": "before_publish"}
            service.log_event(pub, "auth", str(err))
        elif err.retryable:
            _reschedule(pub, err, err.retry_after)
        else:
            pub.status, pub.last_error = "failed", str(err)[:2000]
            pub.remote_status = {**(pub.remote_status or {}), "failure": "before_publish"}
            service.log_event(pub, "failed", str(err))
        queue.complete(db, job_id, wid, {"error": str(err)[:300]})


def _state_saver(pid: int):
    def save(st: dict) -> None:  # own transaction: committed immediately
        with session_scope() as s2:
            p = s2.get(Publication, pid)
            p.upload_state = dict(st)
    return save


def poll_processing() -> None:
    with session_scope() as db:
        # uncertain ones too: a later poll may find the Reel and confirm it
        ids = [p.id for p in db.scalars(select(Publication).where(Publication.status.in_(("processing", "uncertain"))))]
    for pid in ids:
        with session_scope() as db:
            pub = db.get(Publication, pid)
            acct = db.get(PublishAccount, pub.account_id)
            try:
                tokens = service.tokens_for(db, acct)
                if pub.platform == "instagram":  # saves the publish attempt BEFORE calling media_publish
                    st = service.provider(pub.platform).status(_snapshot(pub), tokens, _state_saver(pid))
                else:
                    st = service.provider(pub.platform).status(_snapshot(pub), tokens)
            except PublishError as exc:
                pub.last_error = str(exc)[:2000]
                continue
            except Exception as exc:  # one malformed publication must never stop the polling of the others
                log.exception("status poll failed", extra={"publication_id": pid})
                pub.last_error = f"consulta de status falhou: {type(exc).__name__}"[:2000]
                continue
            pub.remote_status = {**(pub.remote_status or {}), **{k: v for k, v in st.items() if k != "raw"}}
            if st.get("media_id"):  # Instagram: container published -> media id
                pub.upload_state = {**(pub.upload_state or {}), "media_id": st["media_id"]}
                pub.remote_id = st["media_id"]
            if st["state"] == "published":
                pub.status, pub.published_at = "published", pub.published_at or utcnow()
                if st.get("url"):
                    pub.remote_url = st["url"]
                if pub.platform == "tiktok" and st.get("post_id") and acct.handle:
                    pub.remote_url = f"https://www.tiktok.com/@{acct.handle}/video/{st['post_id']}"
                service.log_event(pub, "published", pub.remote_url)
            elif st["state"] == "failed":
                if st.get("auth"):
                    acct.status, acct.last_error = "expired", str(st.get("detail", ""))[:2000]
                pub.status, pub.last_error = "failed", str(st.get("detail", ""))[:2000]
                pub.remote_status = {**(pub.remote_status or {}), "failure": st.get("failure") or "rejected_by_platform"}
                service.log_event(pub, "failed", pub.last_error)
            elif st["state"] == "uncertain":
                if pub.status != "uncertain":
                    service.log_event(pub, "uncertain", str(st.get("detail", "")))
                pub.status, pub.last_error = "uncertain", str(st.get("detail", ""))[:2000]
            elif pub.status == "uncertain" and st.get("media_id"):
                pub.status = "processing"  # reconciled: the Reel was found
                service.log_event(pub, "reconciled", str(st.get("reconciled", "")))


def refresh_metrics(force_ids: list[int] | None = None) -> None:
    cutoff = utcnow() - timedelta(seconds=METRICS_EVERY_S)
    with session_scope() as db:
        q = select(Publication.id).where(Publication.status == "published")
        if force_ids:
            q = q.where(Publication.id.in_(force_ids))
        else:
            q = q.where((Publication.metrics_at.is_(None)) | (Publication.metrics_at < cutoff))
        ids = list(db.scalars(q))
    for pid in ids:
        with session_scope() as db:
            pub = db.get(Publication, pid)
            acct = db.get(PublishAccount, pub.account_id)
            try:
                m = service.provider(pub.platform).metrics(_snapshot(pub), service.tokens_for(db, acct))
            except PublishError as exc:
                pub.last_error = f"métricas: {exc}"[:2000]
                continue
            pub.metrics, pub.metrics_at = m, utcnow()
            if pub.platform == "youtube":
                _add_quota(acct, units=prov_units(pub.platform))


def prov_units(platform: str) -> int:
    return 1 if platform == "youtube" else 0


def tick(state: dict) -> None:
    now = time.monotonic()
    with session_scope() as db:
        heartbeat(db)
        if now - state.get("scan", 0) > SCAN_EVERY_S:
            made = service.auto_scan(db)
            if made:
                log.info("automatic publications scheduled", extra={"count": made})
            state["scan"] = now
        from .editorial import expire_proposals, is_paused

        expire_proposals(db)
        service.enqueue_due(db)
        paused = is_paused(db)
    if now - state.get("poll", 0) > POLL_EVERY_S:
        poll_processing()
        state["poll"] = now
    if now - state.get("metrics", 0) > 900:
        refresh_metrics()
        state["metrics"] = now
    todo = []
    if not paused:  # emergency pause: queued uploads stay queued until it is lifted
        with session_scope() as db:
            jobs = queue.claim(db, worker_id(), ["publish"], has_gpu=False, limit=1, lease_seconds=3600)
            todo = [(j.id, int(j.payload["publication_id"])) for j in jobs]
    for jid, pid in todo:
        run_publish(jid, pid)
    if not todo:  # learning only when no upload is waiting (lowest priority)
        run_learning()


def run_learning() -> None:
    from ..evaluation import feedback

    with session_scope() as db:
        jobs = queue.claim(db, worker_id(), ["learn"], has_gpu=False, limit=1, lease_seconds=1800)
        if not jobs:
            return
        jid = jobs[0].id
    try:
        with session_scope() as db:
            r = feedback.learn(db)
            db.flush()
            out = {"outcome": r.outcome, "labels": r.labels, "detail": r.detail[:300]}
            queue.complete(db, jid, worker_id(), out)
        log.info("learning run", extra=out)
    except Exception as exc:
        log.exception("learning run failed")
        with session_scope() as db:
            queue.fail(db, jid, worker_id(), f"{type(exc).__name__}: {exc}", retryable=False)


def main() -> None:
    s = get_settings()
    init_engine(s.database_url)
    stop = threading.Event()
    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            signal.signal(sig, lambda *_: stop.set())
        except ValueError:
            pass
    with session_scope() as db:
        register(db)
    log.info("publisher started", extra={"worker": worker_id(), "platforms": sorted(service.PROVIDERS)})
    state: dict = {}
    from ..workers.runtime import HEARTBEAT_FILE as beat  # read by `clipper healthcheck worker`

    while not stop.is_set():
        try:
            tick(state)
            beat.write_text(str(time.time()))
        except Exception:
            log.exception("publisher tick failed")
        stop.wait(5.0)
