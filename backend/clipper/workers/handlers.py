"""Job handlers for the audio, scoring, vision and render roles, plus the stream monitor."""

from __future__ import annotations

import asyncio
import logging
import time
from pathlib import Path

from .. import media
from ..analysis.audio_features import compute_features, read_wav_mono
from ..analysis.transcribe import get_transcriber
from ..platforms import probe_live
from ..render.renderer import RenderSpec, render
from ..render.subtitles import SubWord
from ..scoring.engine import score_timeline
from .client import InternalError
from .runtime import JobContext, JobError, WorkerRuntime

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# shared: obtain clip source media
# ---------------------------------------------------------------------------


async def obtain_source(ctx: JobContext, clip_id: int, spec: dict) -> tuple[Path, float]:
    """Download the clip's source (from API storage or the capture worker's segment server).
    When fetched from segments, the source is also stored in the API so re-renders do not
    depend on the capture ring buffer."""
    dest = ctx.workdir / "source.mp4"
    kind = spec.get("source")
    if kind == "api":
        await ctx.client.download(f"/internal/clips/{clip_id}/source", dest)
        return dest, float(spec["source_start"])
    if kind == "segments":
        a, b = spec["range"]
        try:
            headers = await ctx.client.download(f"{spec['segment_base_url']}/extract", dest,
                                                params={"session_id": spec["session_id"], "start": f"{a:.3f}",
                                                        "end": f"{b:.3f}"})
        except InternalError as exc:
            if exc.status == 404:
                raise JobError("source no longer available in capture buffer", retryable=False) from exc
            raise JobError(f"segment server unreachable: {exc}") from exc
        src_start = float(headers.get("x-asc-source-start", a))
        src_dur = float(headers.get("x-asc-source-duration", b - a))
        await ctx.client.upload(f"/internal/clips/{clip_id}/source", dest,
                                params={"source_start": f"{src_start:.3f}", "duration": f"{src_dur:.3f}"})
        return dest, src_start
    raise JobError("clip source is not available anymore", retryable=False)


# ---------------------------------------------------------------------------
# audio
# ---------------------------------------------------------------------------


async def handle_audio(ctx: JobContext) -> dict:
    p = ctx.payload
    sid, idx = int(p["session_id"]), int(p["segment_idx"])
    seg = ctx.workdir / "segment.ts"
    try:
        await ctx.client.download(p["segment_url"], seg, expected_sha=p.get("sha256") or None)
    except InternalError as exc:
        if exc.status == 404:
            await ctx.client.post(f"/internal/sessions/{sid}/analysis", {"segment_idx": idx, "skipped": True})
            return {"skipped": "segment evicted"}
        raise JobError(f"segment download failed: {exc}") from exc
    wav = ctx.workdir / "audio.wav"
    has_audio = await media.extract_wav(seg, wav, threads=2)
    if not has_audio:
        await ctx.client.post(f"/internal/sessions/{sid}/analysis", {"segment_idx": idx, "skipped": True})
        return {"skipped": "no audio stream"}
    samples, sr = await asyncio.to_thread(read_wav_mono, wav)
    feats = await asyncio.to_thread(compute_features, samples, sr)
    ctx.check_cancel()
    s = ctx.settings
    extra, yamnet_s = await asyncio.to_thread(_acoustic_extra, samples, s)
    model = p.get("whisper_model") or s.whisper_model
    t0 = time.monotonic()
    transcriber = await asyncio.to_thread(get_transcriber, model, s.whisper_compute_type, s.whisper_cpu_threads,
                                          s.whisper_model_dir, s.concurrency_audio)
    tr = await asyncio.to_thread(transcriber.transcribe, samples, p.get("language") or "auto")
    stt = time.monotonic() - t0
    words = [[w.start, w.end, w.word, w.probability] for w in tr.words]
    turns, spk_s = await asyncio.to_thread(_speech_turns, samples, words, s)
    body = {
        "segment_idx": idx,
        "features": feats.as_dict(),
        "words": words,
        "speech_ratio": tr.speech_ratio,
        "language": tr.language,
        "extra": extra,
    }
    if turns is not None:
        body["speech_turns"] = turns
    await ctx.client.post(f"/internal/sessions/{sid}/analysis", body)
    return {"words": len(tr.words), "stt_seconds": round(stt, 2), "realtime_factor":
            round(stt / max(0.1, feats.duration), 3), "model": transcriber.name,
            "yamnet_seconds": yamnet_s, "acoustic_model": "yamnet" if extra.get("yamnet") else "none",
            "speaker_turns": len(turns or []), "speaker_seconds": spk_s}


def _speech_turns(samples, words: list, settings) -> tuple[list | None, float]:
    """Voice embedding per spoken turn (None = speaker model not installed on this worker)."""
    from ..ai.speaker import embed_turns, get_embedder

    if not words:
        return [], 0.0
    emb = get_embedder(settings.models_dir, threads=2)
    if emb is None:
        return None, 0.0
    t0 = time.monotonic()
    try:
        out = embed_turns(samples, words, emb)
    except Exception as exc:  # pragma: no cover - never lose the transcription for this
        log.warning("speaker embedding failed", extra={"err": str(exc)[:200]})
        return None, 0.0
    return out, round(time.monotonic() - t0, 2)


def _acoustic_extra(samples, settings) -> tuple[dict, float]:
    """YAMNet categories + voice pitch for one segment (both optional, CPU)."""
    from ..ai.acoustic import get_classifier, pitch_track

    extra: dict = {"pitch_hz": pitch_track(samples), "pitch_hop": 0.25}
    t0 = time.monotonic()
    if settings.yamnet_enabled:
        clf = get_classifier(settings.models_dir)
        if clf is not None:
            y = clf.classify(samples)
            extra["yamnet"] = {"hop": y["hop"], "cats": y["cats"], "top": y["top"]}
    return extra, round(time.monotonic() - t0, 3)


# ---------------------------------------------------------------------------
# scoring
# ---------------------------------------------------------------------------


async def handle_score(ctx: JobContext) -> dict:
    sid = int(ctx.payload["session_id"])
    for _attempt in range(3):
        tl = await ctx.client.get(f"/internal/sessions/{sid}/timeline")
        result = await asyncio.to_thread(score_timeline, tl)
        if result["scored_until"] <= tl["scored_until"] and not result["candidates"]:
            return {"noop": True}
        try:
            r = await ctx.client.post(f"/internal/sessions/{sid}/candidates",
                                      {"from_scored_until": tl["scored_until"], "result": result})
            return {"events": len(result["events"]), "clips": r.get("clip_ids", []),
                    "scored_until": result["scored_until"]}
        except InternalError as exc:
            if exc.status != 409:
                raise
            await asyncio.sleep(0.5)  # concurrent scorer advanced the watermark; rescore
    return {"conflict": True}


# ---------------------------------------------------------------------------
# vision
# ---------------------------------------------------------------------------


async def handle_vision(ctx: JobContext) -> dict:
    """Phase 2 AI analysis: heuristic vision (always) + multimodal model (when available)
    + automatic director + narrative + edit plan. A missing model never fails the clip:
    the worker falls back and reports why."""
    from ..ai import director, edit_plan, narrative
    from ..ai.manager import ModelUnavailable, get_manager
    from ..ai.schemas import VisualAnalysis, VisualEvent
    from ..ai.vlm import FrameInput, VLMAnalyzer
    from ..analysis.vision import HeuristicVisionProvider, sample_frames
    from ..scoring.engine import weighted_total
    from ..settings_store import AISettings

    clip_id = int(ctx.payload["clip_id"])
    spec = await ctx.client.get(f"/internal/clips/{clip_id}/vision-spec")
    timings: dict[str, float] = {}
    t_all = time.monotonic()
    try:
        src, src_start = await obtain_source(ctx, clip_id, spec)
    except JobError as exc:
        if not exc.retryable:
            await ctx.client.post(f"/internal/clips/{clip_id}/vision", {"result": None, "error": str(exc)})
            return {"skipped": str(exc)}
        raise
    timings["fetch_s"] = round(time.monotonic() - t_all, 2)
    s = ctx.settings
    settings = AISettings.model_validate(spec["settings"])
    w0, w1 = float(spec["window"][0]), float(spec["window"][1])
    info = await media.probe(src)
    src_dur = media.duration_of(info)
    w0 = max(w0, src_start)
    w1 = min(w1, src_start + src_dur) if src_dur else w1
    span = max(1.0, w1 - w0)
    peak_rel = float(spec["peak"]) - w0

    # 1. heuristic vision over the whole window (scene cuts, motion centroid, face track)
    t0 = time.monotonic()
    interval = float(ctx.payload.get("frame_interval", 1.0)) * (0.5 if spec.get("deep") else 1.0)
    frames = await asyncio.to_thread(sample_frames, src, w0 - src_start, w1 - src_start, peak_rel + (w0 - src_start),
                                     interval, 180 if spec.get("deep") else 90)
    for f in frames:  # sample_frames returns times relative to its start argument (= window start)
        f.t = round(f.t, 3)
    heur = await asyncio.to_thread(HeuristicVisionProvider().analyze, frames, peak_rel, spec.get("transcript", ""))
    timings["heuristic_vision_s"] = round(time.monotonic() - t0, 2)
    heur_d = heur.as_dict()

    # 2. multimodal model
    model = ctx.payload.get("model")
    manager = get_manager(s.openvino_device, s.ai_allow_cpu_fallback)
    visual: VisualAnalysis | None = None
    fallback_reason = ""
    words_rel = [(w[0] - w0, w[1] - w0, w[2]) for w in spec["words"]]
    cues = _audio_cues(spec, w0)
    if model and (not s.ai_models or model in s.ai_models):
        t0 = time.monotonic()
        try:
            analyzer = VLMAnalyzer(manager, model)
            visual = await asyncio.to_thread(
                analyzer.analyze, [FrameInput(f.t, f.image) for f in frames], peak_rel, span, (w0, w1), words_rel, cues,
                _story_text(spec.get("story") or {}, w0), spec.get("max_frames"),
                (float(spec["setup_t"]) - w0) if spec.get("setup_t") is not None else None)
            timings["deep"] = 1.0 if spec.get("deep") else 0.0
        except ModelUnavailable as exc:
            fallback_reason = str(exc)[:300]
        timings["vlm_s"] = round(time.monotonic() - t0, 2)
    elif model:
        fallback_reason = f"modelo {model} não habilitado neste worker"
    else:
        fallback_reason = "provedor heurístico selecionado"
    if visual is None:
        visual = VisualAnalysis(
            provider=heur.provider, model="", device="CPU", action=min(10.0, heur.visual_score),
            events=[VisualEvent(t=c, label="mudança brusca de cena", importance=5)
                    for c in heur.scene_changes if 0 <= c <= span][:12],
            frame_times=[round(f.t, 2) for f in frames][:60], window_abs=(w0, w1), fallback=True,
            notes=[f"multimodal indisponível: {fallback_reason}"] if fallback_reason else [])

    # 3. director
    cand_d = spec["candidate"]
    breakdown = dict(cand_d.get("breakdown") or {})
    breakdown["visual"] = round(heur.visual_score, 2)
    base = weighted_total(breakdown, settings)
    cand = director.Candidate(float(spec["start"]), float(spec["end"]), float(spec["peak"]), base, breakdown,
                              list(cand_d.get("reasons") or []), list(cand_d.get("kinds") or []))
    ac = spec.get("acoustic") or {}
    track = director.AcousticTrack([(t, c) for t, c in ac.get("frames", [])], [(t, p) for t, p in ac.get("pitch", [])])
    motion = [(w0 + m[0], m[1]) for m in (heur.extra or {}).get("motion", [])]
    hint = None
    sit = (spec.get("story") or {}).get("situation") or {}
    if sit.get("start") is not None and sit["start"] < float(spec["start"]) - 1.0:
        hint = (float(sit["start"]), f"{sit.get('kind', 'situação')}: {sit.get('summary', '')[:60]}")
    decision = director.decide(cand, spec["words"], track, visual, director.Limits(**spec["limits"]), motion=motion,
                               context_hint=hint)
    if visual.fallback:
        decision.reasons.append("Análise multimodal indisponível: decisão com áudio + visão heurística")

    # 4. narrative (the text model consumes facts, never pixels)
    t0 = time.monotonic()
    clip_words = [w[2] for w in spec["words"] if decision.start_abs <= w[0] < decision.end_abs]
    # the streamer's own speech first: other voices (radio, NPCs) are context, not the subject
    sp = list(spec.get("word_speakers") or [])
    sp = sp if len(sp) == len(spec["words"]) else [""] * len(spec["words"])
    own = [w[2] for w, s in zip(spec["words"], sp) if decision.start_abs <= w[0] < decision.end_abs
           and not s.startswith("other:")]
    others = [w[2] for w, s in zip(spec["words"], sp) if decision.start_abs <= w[0] < decision.end_abs
              and s.startswith("other:")]
    narr = await asyncio.to_thread(
        narrative.write, manager, ctx.payload.get("narrative_model") or settings.narrative_model,
        vision_model=model if not visual.fallback else None, transcript=" ".join(own), visual=visual,
        decision=decision, game=(spec.get("streamer") or {}).get("game", ""),
        fallback_titles=list(spec.get("title_suggestions") or []), others=" ".join(others),
        story=_story_text(spec.get("story") or {}, w0))
    timings["narrative_s"] = round(time.monotonic() - t0, 2)

    # 5. edit plan
    first = not spec.get("analysis_runs")
    title = narr.titles[0] if (not narr.fallback and narr.titles and first) else spec.get("title", "")
    motion_cx = [(w0 + m[0], m[2]) for m in (heur.extra or {}).get("motion", [])]
    faces = [{**f, "t": w0 + f["t"]} for f in (heur.extra or {}).get("face_track", [])]
    layout = spec.get("layout")
    plan = edit_plan.build(decision, template_key=settings.edit_template, title=title, transcript_words=clip_words,
                           visual=visual, motion_cx=motion_cx,
                           webcam_region=spec.get("webcam_region") or heur.webcam_region, face_track=faces,
                           layout_override=None if layout in (None, "auto") else layout,
                           normalize_audio=settings.normalize_audio, zoom_intensity=settings.zoom_intensity)
    plan.base_start_abs = decision.start_abs
    timings["total_s"] = round(time.monotonic() - t_all, 2)
    snap = manager.snapshot()
    heur_summary = {k: v for k, v in heur_d.items() if k != "extra"}
    heur_summary.update({"motion_points": len((heur.extra or {}).get("motion", [])), "faces": len(faces)})
    await ctx.client.post(f"/internal/clips/{clip_id}/analysis", {
        "decision": decision.model_dump(), "edit_plan": plan.model_dump(), "visual": visual.model_dump(),
        "narrative": narr.model_dump(), "heuristic_vision": heur_summary,
        "meta": {"vision_model": None if visual.fallback else model, "vision_device": visual.device,
                 "vision_provider": visual.provider, "narrative_provider": narr.provider,
                 "fallback_reason": fallback_reason, "timings": timings, "frames_sampled": len(frames),
                 "frames_to_model": 0 if visual.fallback else len(visual.frame_times),
                 "model_state": (snap.get("models") or {}).get(model or "", {}),
                 "prompt_version": _prompt_version(), "director_version": _director_version(),
                 "max_new_tokens": _max_tokens(model), "frames": list(visual.frame_times)[:16]},
    })
    return {"mode": decision.mode, "keep": decision.keep, "score": decision.score, "vision": visual.provider,
            "narrative": narr.provider, "timings": timings}


def _story_text(story: dict, w0: float) -> str:
    """Bounded story memory for the VLM (session times converted to the analysed window)."""
    sit = story.get("situation") or {}
    lines = []
    for b in (story.get("before") or [])[-2:]:
        lines.append(f"antes: {b[:160]}")
    if sit:
        lines.append(f"situação em andamento: {sit.get('summary', '')[:200]} (confiança {sit.get('confidence', 0):.1f})")
    return " | ".join(lines)[:500]


def _prompt_version() -> str:
    from ..ai.vlm import PROMPT_VERSION

    return PROMPT_VERSION


def _director_version() -> str:
    from ..ai import director

    return getattr(director, "DIRECTOR_VERSION", "director-2")


def _max_tokens(model: str | None) -> int | None:
    from ..ai.registry import MODELS

    spec = MODELS.get(model or "")
    return getattr(spec, "max_new_tokens", None) if spec else None


def _audio_cues(spec: dict, w0: float) -> list[str]:
    """Short, factual acoustic hints for the VLM prompt (window-relative seconds)."""
    names = {"laughter": "risada", "scream": "grito", "shout": "fala gritada", "game_impact": "explosão/impacto",
             "gunfire": "tiros", "vehicle": "veículo/sirene", "crowd": "torcida"}
    cues: list[str] = []
    last: dict[str, float] = {}
    for t, cats in (spec.get("acoustic") or {}).get("frames", []):
        for c, label in names.items():
            if cats.get(c, 0) >= 0.4 and t - last.get(c, -99.0) > 3:
                cues.append(f"{label} em {t - w0:.1f}s")
                last[c] = t
    for r in (spec.get("candidate") or {}).get("reasons", [])[:3]:
        cues.append(r)
    return cues[:10]


# ---------------------------------------------------------------------------
# render
# ---------------------------------------------------------------------------


async def handle_render(ctx: JobContext) -> dict:
    clip_id = int(ctx.payload["clip_id"])
    spec = await ctx.client.get(f"/internal/clips/{clip_id}/render-spec")
    if int(spec["version"]) > int(ctx.payload.get("version", spec["version"])):
        return {"skipped": "newer version queued"}
    src, src_start = await obtain_source(ctx, clip_id, spec)
    ctx.check_cancel()
    s = ctx.settings
    rs = RenderSpec(
        source=src, source_start=src_start, start=float(spec["start"]), end=float(spec["end"]),
        peak=float(spec["peak"]), words=[SubWord(float(w[0]), float(w[1]), str(w[2]), str(w[3]) if len(w) > 3 else "")
                                         for w in spec["words"]],
        title=spec.get("title", ""), caption_style=spec.get("caption_style", "bold"),
        layout=spec.get("layout", "auto"), webcam_region=spec.get("webcam_region"),
        show_title=bool(spec.get("show_title", True)), preset=spec.get("preset", "veryfast"),
        crf=int(spec.get("crf", 21)), fps=int(spec.get("fps", 30)), threads=s.ffmpeg_threads,
        plan=spec.get("edit_plan"), captions=spec.get("captions"),
    )
    try:
        res = await render(rs, ctx.workdir / "render")
    except media.MediaError as exc:
        raise JobError(f"ffmpeg render failed: {exc}") from exc
    info = await media.probe(res.video)
    vs = media.video_stream(info) or {}
    if (int(vs.get("width", 0)), int(vs.get("height", 0))) != (1080, 1920):
        raise JobError(f"unexpected output size {vs.get('width')}x{vs.get('height')}", retryable=False)
    version = int(spec["version"])
    await ctx.client.upload(f"/internal/clips/{clip_id}/thumbnail", res.thumbnail, params={"version": version})
    r = await ctx.client.upload(f"/internal/clips/{clip_id}/video", res.video,
                                params={"version": version, "seconds": f"{res.seconds:.2f}", "layout": res.layout})
    return {"seconds": round(res.seconds, 2), "duration": round(res.duration, 2), "layout": res.layout,
            "stale": bool(r.get("stale")), "applied": res.applied, "audio": res.audio}


# ---------------------------------------------------------------------------
# evaluation dataset media (Phase 3): extract, re-encode to 720p, upload
# ---------------------------------------------------------------------------


async def handle_dataset_media(ctx: JobContext) -> dict:
    eid = int(ctx.payload["example_id"])
    try:
        spec = await ctx.client.get(f"/internal/examples/{eid}/media-spec")
    except InternalError as exc:
        if exc.status == 404:
            return {"skipped": "example gone"}
        raise
    raw = ctx.workdir / "raw.mp4"
    out = ctx.workdir / "example.mp4"
    try:
        if spec["source"] == "segments":
            a, b = spec["range"]
            headers = await ctx.client.download(f"{spec['segment_base_url']}/extract", raw,
                                                params={"session_id": spec["session_id"], "start": f"{a:.3f}",
                                                        "end": f"{b:.3f}"})
            src_start = float(headers.get("x-asc-source-start", a))
            trim = (0.0, None)
        else:  # clip source kept by the API (with margins)
            await ctx.client.download(f"/internal/examples/{eid}/source", raw)
            src_start = float(spec["source_start"])
            a, b = spec["range"]
            trim = (max(0.0, a - src_start), b - a)
    except InternalError as exc:
        if exc.status == 404:
            await ctx.client.post(f"/internal/examples/{eid}/media-failed",
                                  {"error": "mídia não está mais disponível no buffer de captura"})
            return {"failed": "source gone"}
        raise
    # small review copy: 720p30, CRF 28 (~1 Mbit/s) - the dataset must not eat the disk
    cmd = [media.FFMPEG, "-hide_banner", "-loglevel", "error", "-nostdin", "-y"]
    if trim[0] > 0:
        cmd += ["-ss", f"{trim[0]:.3f}"]
    cmd += ["-i", str(raw)]
    if trim[1] is not None:
        cmd += ["-t", f"{trim[1]:.3f}"]
    cmd += ["-vf", "scale=-2:'min(720,ih)',fps=30", "-c:v", "libx264", "-preset", "veryfast", "-crf", "28",
            "-c:a", "aac", "-b:a", "96k", "-movflags", "+faststart", str(out)]
    try:
        await media.run(cmd, timeout=600)
    except media.MediaError as exc:
        raise JobError(f"ffmpeg failed: {str(exc)[-300:]}") from exc
    info = await media.probe(out)
    dur = media.duration_of(info) or 0.0
    await ctx.client.upload(f"/internal/examples/{eid}/media", out,
                            params={"start": f"{src_start + trim[0]:.3f}", "duration": f"{dur:.3f}"})
    return {"bytes": out.stat().st_size, "duration": dur}


# ---------------------------------------------------------------------------
# monitor (background loop, not a queued job)
# ---------------------------------------------------------------------------


async def monitor_loop(rt: WorkerRuntime, interval: float = 60.0, concurrency: int = 4) -> None:
    sem = asyncio.Semaphore(concurrency)

    async def check(t: dict) -> None:
        async with sem:
            note = channel_id = ""
            if t["platform"] == "file":
                live, title, error = True, "", ""
            else:
                st = await asyncio.to_thread(probe_live, t["url"])
                live, title, error, note, channel_id = st.live, st.title, st.error, st.note, st.channel_id
            try:
                r = await rt.client.post("/internal/monitor/report", {"streamer_id": t["streamer_id"], "live": live,
                                                                      "title": title, "error": error, "note": note,
                                                                      "channel_id": channel_id})
                if r.get("created"):
                    log.info("live detected, session created", extra={"streamer_id": t["streamer_id"],
                                                                       "session_id": r.get("session_id")})
            except InternalError as exc:
                log.warning("monitor report failed", extra={"err": str(exc)})

    while True:
        try:
            targets = await rt.client.get("/internal/monitor/targets", min_interval=interval)
            await asyncio.gather(*(check(t) for t in targets))
        except InternalError as exc:
            log.warning("monitor poll failed", extra={"err": str(exc)})
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("monitor loop error")
        await asyncio.sleep(5.0)
