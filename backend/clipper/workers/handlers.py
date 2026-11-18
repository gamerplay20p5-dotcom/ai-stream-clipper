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
    model = p.get("whisper_model") or s.whisper_model
    t0 = time.monotonic()
    transcriber = await asyncio.to_thread(get_transcriber, model, s.whisper_compute_type, s.whisper_cpu_threads,
                                          s.whisper_model_dir)
    tr = await asyncio.to_thread(transcriber.transcribe, samples, p.get("language") or "auto")
    stt = time.monotonic() - t0
    await ctx.client.post(f"/internal/sessions/{sid}/analysis", {
        "segment_idx": idx,
        "features": feats.as_dict(),
        "words": [[w.start, w.end, w.word, w.probability] for w in tr.words],
        "speech_ratio": tr.speech_ratio,
        "language": tr.language,
    })
    return {"words": len(tr.words), "stt_seconds": round(stt, 2), "realtime_factor":
            round(stt / max(0.1, feats.duration), 3), "model": transcriber.name}


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
    from ..analysis.vision import get_provider, sample_frames

    clip_id = int(ctx.payload["clip_id"])
    spec = await ctx.client.get(f"/internal/clips/{clip_id}/vision-spec")
    try:
        src, src_start = await obtain_source(ctx, clip_id, spec)
    except JobError as exc:
        if not exc.retryable:
            await ctx.client.post(f"/internal/clips/{clip_id}/vision", {"result": None, "error": str(exc)})
            return {"skipped": str(exc)}
        raise
    s = ctx.settings
    provider_name = ctx.payload.get("provider") or s.vision_provider
    provider = await asyncio.to_thread(get_provider, provider_name, s.openvino_model_dir, s.openvino_device)
    rel0 = spec["start"] - src_start
    frames = await asyncio.to_thread(sample_frames, src, rel0, spec["end"] - src_start, spec["peak"] - src_start,
                                     float(ctx.payload.get("frame_interval", 1.0)))
    t0 = time.monotonic()
    result = await asyncio.to_thread(provider.analyze, frames, spec["peak"] - spec["start"], spec.get("transcript", ""))
    result.latency_s = round(time.monotonic() - t0, 3)
    await ctx.client.post(f"/internal/clips/{clip_id}/vision", {"result": result.as_dict()})
    return {"provider": result.provider, "visual_score": result.visual_score, "frames": result.frames_analyzed,
            "latency_s": result.latency_s}


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
        peak=float(spec["peak"]), words=[SubWord(float(w[0]), float(w[1]), str(w[2])) for w in spec["words"]],
        title=spec.get("title", ""), caption_style=spec.get("caption_style", "bold"),
        layout=spec.get("layout", "auto"), webcam_region=spec.get("webcam_region"),
        show_title=bool(spec.get("show_title", True)), preset=spec.get("preset", "veryfast"),
        crf=int(spec.get("crf", 21)), fps=int(spec.get("fps", 30)), threads=s.ffmpeg_threads,
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
            "stale": bool(r.get("stale"))}


# ---------------------------------------------------------------------------
# monitor (background loop, not a queued job)
# ---------------------------------------------------------------------------


async def monitor_loop(rt: WorkerRuntime, interval: float = 60.0, concurrency: int = 4) -> None:
    sem = asyncio.Semaphore(concurrency)

    async def check(t: dict) -> None:
        async with sem:
            if t["platform"] == "file":
                live, title, error = True, "", ""
            else:
                st = await asyncio.to_thread(probe_live, t["url"])
                live, title, error = st.live, st.title, st.error
            try:
                r = await rt.client.post("/internal/monitor/report", {"streamer_id": t["streamer_id"], "live": live,
                                                                      "title": title, "error": error})
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
