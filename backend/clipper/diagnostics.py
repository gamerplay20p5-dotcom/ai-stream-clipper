"""Pipeline diagnostics and manual test clips.

Every state and explanation is derived from real data (jobs, worker heartbeats, segments,
transcript, scoring statistics, clips) - never guessed. Manual test clips ("TESTE MANUAL")
go through the real pipeline but scoring can never block them, and they are excluded from
quality metrics.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from . import queue
from .db import utcnow
from .models import AudioFeature, Clip, Job, Segment, Streamer, StreamSession, TranscriptWord, Worker
from .settings_store import load_ai_settings

ROLE_OF = {"capture": "capture", "audio_analyze": "audio", "score": "scoring", "vision_analyze": "vision",
           "render": "render"}
REASON_PT = {
    "below_min_score": "abaixo da nota mínima",
    "duplicate": "duplicado de um corte existente",
    "text_only": "só fala, sem reação acústica",
    "rate_limit": "limite de cortes por hora",
    "too_short": "trecho curto demais",
}
MAX_TESTS_IN_FLIGHT = 2
TEST_RANGES = {"last30": 30, "last60": 60, "last90": 90}


class TestClipError(ValueError):
    def __init__(self, message: str, status: int = 422):
        super().__init__(message)
        self.status = status


def _iso(dt):
    return dt.isoformat() + "Z" if dt else None


def online_roles(db: Session, stale_s: float = 45.0) -> dict[str, list[str]]:
    cutoff = utcnow() - timedelta(seconds=stale_s)
    out: dict[str, list[str]] = defaultdict(list)
    for w in db.scalars(select(Worker).where(Worker.last_heartbeat_at >= cutoff)):
        for r in w.roles or []:
            out[r].append(w.id)
    return dict(out)


def buffer_window(db: Session, session_id: int) -> tuple[float, float] | None:
    row = db.execute(select(func.min(Segment.start), func.max(Segment.start + Segment.duration))
                     .where(Segment.session_id == session_id, Segment.available.is_(True))).one()
    return (float(row[0]), float(row[1])) if row[0] is not None else None


def latest_session(db: Session, streamer_id: int) -> StreamSession | None:
    return db.scalar(select(StreamSession).where(StreamSession.streamer_id == streamer_id)
                     .order_by((StreamSession.status == "live").desc(), StreamSession.id.desc()))


def _jobs_for_session(db: Session, sess: StreamSession, clip_ids: set[int]) -> list[Job]:
    since = sess.started_at - timedelta(minutes=1)
    out = []
    for j in db.scalars(select(Job).where(Job.created_at >= since)):
        p = j.payload or {}
        if p.get("session_id") == sess.id or p.get("clip_id") in clip_ids:
            out.append(j)
    return out


def session_report(db: Session, s: Streamer, roles: dict[str, list[str]], settings) -> dict:
    sess = latest_session(db, s.id)
    rep: dict = {"streamer": {"id": s.id, "name": s.display_name or s.channel, "platform": s.platform,
                              "enabled": s.enabled, "status": s.status, "last_checked_at": _iso(s.last_checked_at),
                              "last_error": s.last_error}}
    if sess is None:
        rep.update(stage="Sem transmissão", state="idle",
                   explanation="Nenhuma sessão capturada ainda." + (" O canal está offline." if s.status == "offline"
                                                                    else ""))
        return rep
    now = utcnow()
    clips = db.scalars(select(Clip).where(Clip.session_id == sess.id)).all()
    real = [c for c in clips if not c.is_test]
    clip_ids = {c.id for c in clips}
    jobs = _jobs_for_session(db, sess, clip_ids)
    by = defaultdict(lambda: defaultdict(int))
    durations = defaultdict(list)
    oldest_queued: dict[str, float] = {}
    for j in jobs:
        by[j.type][j.status] += 1
        if j.status == "done" and j.started_at and j.finished_at and j.type != "capture":
            durations[j.type].append((j.finished_at - j.started_at).total_seconds())
        if j.status == "queued":
            age = (now - j.created_at).total_seconds()
            oldest_queued[j.type] = max(oldest_queued.get(j.type, 0), age)
    last_seg = db.scalar(select(Segment).where(Segment.session_id == sess.id).order_by(Segment.idx.desc()))
    buf = buffer_window(db, sess.id)
    last_word = db.scalar(select(func.max(TranscriptWord.start)).where(TranscriptWord.session_id == sess.id))
    feats = db.execute(select(func.count(), func.max(AudioFeature.start))
                       .where(AudioFeature.session_id == sess.id)).one()
    with_vision = [c for c in clips if c.analysis_meta]
    last_vis = max(with_vision, key=lambda c: c.analysis_meta.get("at", ""), default=None)
    rendered = [c for c in clips if c.video_path]
    last_render = max(rendered, key=lambda c: c.updated_at, default=None)
    stats = sess.stats or {}
    counts = stats.get("rejected_counts") or {}
    rejected_clips = [c for c in real if c.status == "rejected"]
    dead = [j for j in jobs if j.status == "dead"]

    seg_age = (now - last_seg.captured_at).total_seconds() if last_seg else None
    stage, state, why = _derive(sess, s, by, oldest_queued, roles, seg_age, real, rejected_clips, counts, stats,
                                settings, dead, clips)
    rep.update({
        "session": {"id": sess.id, "status": sess.status, "started_at": _iso(sess.started_at),
                    "covered_until": round(sess.covered_until, 1), "analyzed_until": round(sess.analyzed_until, 1),
                    "scored_until": round(sess.scored_until, 1), "segment_count": sess.segment_count},
        "last_segment": {"idx": last_seg.idx, "captured_at": _iso(last_seg.captured_at),
                         "age_s": round(seg_age, 1)} if last_seg else None,
        "buffer": {"start": round(buf[0], 1), "end": round(buf[1], 1), "seconds": round(buf[1] - buf[0], 1)}
        if buf else None,
        "last_transcript_t": round(last_word, 1) if last_word is not None else None,
        "audio_features": {"segments": feats[0], "last_t": feats[1]},
        "last_visual": {"clip_id": last_vis.id, "at": last_vis.analysis_meta.get("at"),
                        "model": last_vis.analysis_meta.get("vision_model"),
                        "fallback": last_vis.analysis_meta.get("vision_fallback"),
                        "fallback_reason": last_vis.analysis_meta.get("fallback_reason")} if last_vis else None,
        "scoring": {"last_scored_at": stats.get("last_scored_at"), "min_score": settings.min_score,
                    "candidates": len(real), "rejected_by_reason": counts,
                    "rejected_by_director": len(rejected_clips),
                    "best_rejected_score": stats.get("best_rejected_score"),
                    "recent_rejected": (stats.get("recent_rejected") or [])[-8:]},
        "jobs": {t: dict(v) for t, v in by.items()},
        "oldest_queued_s": {t: round(v) for t, v in oldest_queued.items()},
        "failures": [{"id": j.id, "type": j.type, "error": (j.last_error or "")[-300:], "attempts": j.attempts}
                     for j in dead[-10:]],
        "last_render": {"clip_id": last_render.id, "at": _iso(last_render.updated_at),
                        "is_test": last_render.is_test} if last_render else None,
        "latency_s": {t: round(sum(v) / len(v), 2) for t, v in durations.items() if v},
        "stage": stage, "state": state, "explanation": why,
    })
    return rep


def _derive(sess, s, by, oldest, roles, seg_age, real, rejected_clips, counts, stats, settings, dead, clips):
    """(stage label, state, explanation) from the collected facts."""
    def pending(t):
        return by[t].get("queued", 0) + by[t].get("running", 0)

    def missing(role):
        return not roles.get(role)

    live = sess.status == "live"
    if live and missing("capture"):
        return "Falha", "error", "Worker de captura indisponível: nenhum heartbeat recente de um worker 'capture'."
    if live and seg_age is not None and seg_age > 90:
        return "Falha", "error", f"A captura não recebe segmentos novos há {seg_age:.0f} s."
    for t, role, label in (("audio_analyze", "audio", "áudio/transcrição"), ("score", "scoring", "pontuação"),
                           ("render", "render", "renderização")):
        if pending(t) and missing(role):
            return ("Falha", "error",
                    f"Worker de {label} indisponível: {pending(t)} tarefa(s) aguardando há até {oldest.get(t, 0):.0f} s.")
    if dead:
        kinds = sorted({j.type for j in dead})
        return "Falha", "error", f"{len(dead)} tarefa(s) falharam definitivamente ({', '.join(kinds)}). Use 'Reprocessar falhas'."
    if any(c.status == "rendering" for c in clips):
        running = by["render"].get("running", 0)
        return ("Renderizando" if running else "Aguardando renderização", "working",
                f"{sum(1 for c in clips if c.status == 'rendering')} corte(s) na renderização.")
    if any(c.status == "analyzing" for c in clips):
        n = sum(1 for c in clips if c.status == "analyzing")
        if missing("vision"):
            return ("Analisando vídeo", "waiting",
                    f"{n} candidato(s) aguardando análise visual; nenhum worker de visão online, seguem com fallback "
                    f"após {settings.vision_timeout_seconds} s.")
        return "Analisando vídeo", "working", f"{n} candidato(s) na análise visual."
    if pending("score"):
        return "Calculando pontuação", "working", f"{pending('score')} tarefa(s) de pontuação na fila."
    if pending("audio_analyze"):
        state = "Analisando áudio" if by["audio_analyze"].get("running") else "Aguardando transcrição"
        return state, "working", f"{pending('audio_analyze')} segmento(s) aguardando transcrição/análise de áudio."
    if not real:
        if not stats.get("last_scored_at"):
            return ("Capturando" if live else "Concluído", "ok",
                    "Ainda não houve pontuação: é preciso acumular contexto após o evento (≈ "
                    f"{settings.post_context_seconds:.0f} s) antes de avaliar.")
        total = sum(counts.values())
        if total == 0:
            return ("Capturando" if live else "Concluído", "ok",
                    "Nenhum momento com reação detectado até agora (sem picos de volume, gritos, risadas ou falas de "
                    "reação fortes).")
        parts = ", ".join(f"{n} {REASON_PT.get(r, r)}" for r, n in sorted(counts.items(), key=lambda x: -x[1]))
        best = stats.get("best_rejected_score")
        best_txt = f" A melhor nota foi {best:.1f}." if best is not None else ""
        return ("Capturando" if live else "Concluído", "ok",
                f"Nenhum candidato atingiu a nota mínima de {settings.min_score:.0f}.{best_txt} "
                f"Momentos avaliados: {parts}.")
    ready = sum(1 for c in real if c.status in ("ready", "approved"))
    disc = len(rejected_clips)
    return ("Capturando" if live else "Concluído", "ok",
            f"{len(real)} candidato(s): {ready} pronto(s), {disc} descartado(s) pelo diretor.")


def report(db: Session) -> dict:
    settings = load_ai_settings(db)
    roles = online_roles(db)
    streamers = db.scalars(select(Streamer).order_by(Streamer.id)).all()
    return {"generated_at": _iso(utcnow()), "workers_online_by_role": roles,
            "queue": queue.queue_stats(db),
            "streamers": [session_report(db, s, roles, settings) for s in streamers]}


def retry_failed(db: Session, streamer_id: int) -> list[int]:
    """Re-queue dead jobs of the streamer's latest session (same job rows and idempotency
    keys -> no duplicates; candidate insertion is protected by the scoring watermark)."""
    s = db.get(Streamer, streamer_id)
    sess = latest_session(db, streamer_id) if s else None
    if sess is None:
        return []
    clip_ids = {c for (c,) in db.execute(select(Clip.id).where(Clip.session_id == sess.id))}
    ids = []
    for j in _jobs_for_session(db, sess, clip_ids):
        if j.status == "dead" and queue.retry(db, j.id) is not None:
            ids.append(j.id)
            if j.type == "render":
                clip = db.get(Clip, (j.payload or {}).get("clip_id"))
                if clip is not None and clip.status == "failed":
                    clip.status, clip.error = "rendering", ""
    return ids


# ---------------------------------------------------------------------------
# manual test clips
# ---------------------------------------------------------------------------


def create_test_clip(db: Session, streamer_id: int, mode: str, start: float | None, end: float | None,
                     requested_by: str) -> Clip:
    from .pipeline import enqueue_render
    from .pipeline_ai import enqueue_analysis

    s = db.get(Streamer, streamer_id)
    if s is None:
        raise TestClipError("streamer não encontrado", 404)
    sess = latest_session(db, streamer_id)
    buf = buffer_window(db, sess.id) if sess else None
    if sess is None or buf is None or buf[1] - buf[0] < 5:
        raise TestClipError("Não há vídeo suficiente no buffer deste streamer (é preciso uma sessão capturada).", 409)
    lo, hi = buf
    if mode in TEST_RANGES:
        e = hi
        st = max(lo, hi - TEST_RANGES[mode])
    elif mode == "custom":
        if start is None or end is None:
            raise TestClipError("informe início e fim do intervalo")
        st, e = float(start), float(end)
        if not (lo - 0.01 <= st < e <= hi + 0.01):
            raise TestClipError(f"intervalo fora do buffer disponível ({lo:.1f}s a {hi:.1f}s)")
    else:
        raise TestClipError("modo inválido")
    if not 5 <= e - st <= 180:
        raise TestClipError("o trecho deve ter entre 5 e 180 segundos")
    in_flight = db.scalar(select(func.count()).select_from(Clip).where(
        Clip.is_test.is_(True), Clip.status.in_(("candidate", "analyzing", "rendering"))))
    same = db.scalar(select(Clip).where(Clip.is_test.is_(True), Clip.session_id == sess.id,
                                        func.abs(Clip.start - st) < 1.0, func.abs(Clip.end - e) < 1.0,
                                        Clip.status.in_(("candidate", "analyzing", "rendering", "ready", "approved"))))
    if same is not None:
        return same  # idempotent: double click / repeated request returns the same test
    if in_flight >= MAX_TESTS_IN_FLIGHT:
        raise TestClipError(f"já existem {in_flight} cortes de teste em processamento; aguarde", 429)
    settings = load_ai_settings(db)
    words = db.execute(select(TranscriptWord.word).where(TranscriptWord.session_id == sess.id,
                                                         TranscriptWord.start >= st, TranscriptWord.start < e)
                       .order_by(TranscriptWord.start)).all()

    def mmss(t):
        return f"{int(t // 60)}:{int(t % 60):02d}"

    clip = Clip(session_id=sess.id, streamer_id=s.id, start=round(st, 2), end=round(e, 2), peak=round((st + e) / 2, 2),
                score=0.0, score_breakdown={}, kinds=["manual_test"], is_test=True,
                reasons=[f"TESTE MANUAL solicitado por {requested_by}: {mmss(st)}–{mmss(e)} da sessão #{sess.id}"],
                transcript=" ".join(w[0] for w in words), title=f"TESTE MANUAL {s.display_name or s.channel} "
                f"{mmss(st)}-{mmss(e)}", description="Corte de teste gerado manualmente (não é seleção da IA).",
                caption_style=settings.caption_style, layout=settings.default_layout, version=1,
                heuristic_start=round(st, 2), heuristic_end=round(e, 2))
    db.add(clip)
    db.flush()
    if settings.vision_enabled:
        enqueue_analysis(db, clip, settings)
    else:
        enqueue_render(db, clip)
    return clip
