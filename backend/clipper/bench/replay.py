"""Compare visual discovery off/on on local media, through the production Quality Gate."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import time
from dataclasses import asdict
from pathlib import Path
from secrets import token_hex
from shutil import copyfile
from tempfile import TemporaryDirectory
from typing import Annotated

import httpx
from fastapi import FastAPI
from pydantic import AliasChoices, BaseModel, Field, model_validator
from sqlalchemy import select

from .. import media, pipeline
from ..ai.manager import get_manager
from ..ai.quality_gate import READY, VERSION as GATE_VERSION
from ..ai.schemas import VisualAnalysis
from ..ai.visual_evidence import confirmed_event_kind, confirmed_occurrence
from ..api.routes_internal import router
from ..config import Settings, get_settings, reset_settings_cache
from ..db import Base, init_engine, session_scope
from ..models import AudioFeature, Clip, Streamer, StreamSession
from ..security import sha256_file
from ..settings_store import AISettings, save_ai_settings
from ..workers import handlers
from ..workers.capture import CaptureManager, _read_csv
from ..workers.client import InternalClient
from ..workers.runtime import JobContext
from ..workers.segment_server import create_segment_app
from ..workers.segment_store import LocalSegment, SegmentStore

ORIGIN = "http://replay.invalid"
WORKER = "local-replay"
JOB_HANDLERS = {"audio_analyze": handlers.handle_audio, "score": handlers.handle_score,
                "vision_analyze": handlers.handle_vision}
Seconds = Annotated[float, Field(ge=0, allow_inf_nan=False)]


class Annotation(BaseModel):
    peak: Seconds = Field(validation_alias=AliasChoices("peak", "t"))
    label: str = ""
    must_include: tuple[Seconds, Seconds] | None = None
    clip_worthy: bool = True


class RecordedSegment(BaseModel):
    file: Path
    start: Seconds
    duration: float = Field(gt=0, allow_inf_nan=False)


class Case(BaseModel):
    video: Path | None = None
    segments: list[RecordedSegment] = Field(default_factory=list)
    events: list[Annotation]  # an explicit empty list is a negative control
    language: str = "pt"
    game: str = ""

    @model_validator(mode="after")
    def _source(self):
        if bool(self.video) == bool(self.segments):
            raise ValueError("provide exactly one of video or segments")
        for prev, current in zip(self.segments, self.segments[1:]):
            if current.start <= prev.start or abs(current.start - prev.start - prev.duration) >= 0.75:
                raise ValueError("segments must follow one contiguous capture run in timestamp order")
        return self


class Manifest(BaseModel):
    videos: list[Case] = Field(min_length=1)


def _local_path(root: Path, path: Path) -> Path:
    path = root / path
    if str(path).startswith(("//", "\\\\")):
        raise ValueError(f"expected a local file: {path}")
    path = path.resolve(strict=True)
    if not path.is_file():
        raise ValueError(f"expected a local file: {path}")
    return path


async def _prepare(case: Case, folder: Path, capture: CaptureManager) -> list[LocalSegment]:
    folder.mkdir()
    rows = case.segments
    if case.video is not None and case.video.suffix.lower() == ".ts":
        info = await media.probe(case.video)
        rows = [RecordedSegment(file=case.video, start=0, duration=media.duration_of(info))]
    elif case.video is not None:
        args = capture._ffmpeg_cmd(["-protocol_whitelist", "file,crypto", "-i", str(case.video)], 1, 0)
        await media.run(args, cwd=folder)
        csv_rows = _read_csv(folder / "run_1.csv")
        if csv_rows:
            offset = csv_rows[0][1]
            rows = [RecordedSegment(file=folder / name, start=round(start - offset, 3), duration=round(end - start, 3))
                    for name, start, end in csv_rows]
    if not rows:
        raise ValueError("source contains no video segments")
    start, end = rows[0].start, rows[-1].start + rows[-1].duration
    for event in case.events:
        if not start <= event.peak < end or (
                event.must_include and not start <= event.must_include[0] <= event.peak <= event.must_include[1] <= end):
            raise ValueError(f"annotation at {event.peak:g}s is outside source coverage or its required interval")
    segments = []
    for idx, row in enumerate(rows):
        path = row.file
        if path.parent != folder:
            path = Path(copyfile(path, folder / f"seg_{idx:06}.ts"))
        segments.append(LocalSegment(idx, 1, row.start, row.duration, str(path), path.stat().st_size, sha256_file(path)))
    return segments


async def _drain(client: InternalClient, settings: Settings, *types: str) -> list[dict]:
    runs = []
    while jobs := await client.post("/internal/jobs/claim", {"types": types}):
        job = jobs[0]
        jid, kind = job["id"], job["type"]
        with TemporaryDirectory(prefix=f"job_{jid}-", dir=settings.work_dir) as folder:
            ctx = JobContext(jid, kind, job["payload"], job["attempts"], client, settings, Path(folder),
                             max_attempts=job["max_attempts"])
            result = await JOB_HANDLERS[kind](ctx)
        if not (await client.post(f"/internal/jobs/{jid}/complete", {"result": result}))["ok"]:
            raise RuntimeError(f"job {jid} could not be completed")
        runs.append({"job": kind, **result})
    return runs


def _match(events: list[Annotation], peaks: list[float], tolerance: float) -> dict[int, Annotation]:
    truth = sorted((e for e in events if e.clip_worthy), key=lambda e: e.peak)
    guesses = sorted(enumerate(peaks), key=lambda p: p[1])
    matched = {}
    i = j = 0
    while i < len(truth) and j < len(guesses):
        index, peak = guesses[j]
        delta = peak - truth[i].peak
        if abs(delta) <= tolerance:
            matched[index] = truth[i]
            i += 1
            j += 1
        elif delta < 0:
            j += 1
        else:
            i += 1
    return matched


def _compare(events: list[Annotation], peaks: list[float], tolerance: float) -> dict:
    n = len(_match(events, peaks, tolerance))
    return {"tp": n, "fp": len(peaks) - n, "fn": sum(e.clip_worthy for e in events) - n}


def _clip_result(clip: Clip, annotation: Annotation | None) -> dict:
    analysis = (clip.analysis or {}).get("visual")
    visual = VisualAnalysis.model_validate(analysis) if analysis else None
    meta = clip.analysis_meta or {}
    required = annotation.must_include if annotation else None
    return {"start": clip.start, "peak": clip.peak, "end": clip.end, "score": clip.score, "title": clip.title,
            "matched_annotation": annotation.peak if annotation else None,
            "covers_required_interval": clip.start <= required[0] and clip.end >= required[1] if required else None,
            "kind": confirmed_event_kind(visual, clip.peak, clip.start, clip.end) if visual else "unknown",
            "confirmation": visual.confirmation if visual else None,
            "targeted_confirmed_in_clip": confirmed_occurrence(visual, clip.start, clip.end),
            "vlm_model": meta.get("vision_model"),
            "vlm_device": meta.get("vision_device"), "vlm_fallback": not visual or visual.fallback,
            "fallback_reason": meta.get("fallback_reason"), "timings": meta.get("timings"),
            "gate": (clip.decision or {}).get("gate"), "gate_history": (clip.provenance or {}).get("gate", {}).get("history"),
            "status": clip.status}


async def _replay(case: Case, segments: list[LocalSegment], mode: str, settings: Settings, ai: AISettings,
                  store: SegmentStore, client: InternalClient, tolerance: float) -> dict:
    t0 = time.monotonic()
    offset = segments[0].start
    with session_scope() as db:
        save_ai_settings(db, ai.model_copy(update={"visual_discovery": mode}))
        streamer = Streamer(platform="file", channel=token_hex(8), url="file://replay", display_name="Replay local",
                            preferences={"language": case.language, "game": case.game})
        db.add(streamer)
        db.flush()
        session = StreamSession(streamer_id=streamer.id, scored_until=offset, analyzed_until=offset,
                                base_profile={"language": case.language, "whisper_model": ai.whisper_model})
        db.add(session)
        db.flush()
        sid = session.id
        for seg in segments:
            store.add(sid, seg)
        pipeline.register_segments(db, sid, WORKER, ORIGIN, [asdict(seg) for seg in segments])
    jobs = await _drain(client, settings, "audio_analyze")
    await client.post(f"/internal/sessions/{sid}/end", {"reason": "local replay"})
    jobs += await _drain(client, settings, "score", "vision_analyze")
    with session_scope() as db:
        session = db.get(StreamSession, sid)
        features = db.scalars(select(AudioFeature).where(AudioFeature.session_id == sid)
                              .order_by(AudioFeature.segment_idx))
        hypotheses = [{**h, **{k: round(f.start + h[k], 3) for k in ("start", "peak", "end")}}
                      for f in features for h in (f.extra or {}).get("visual_motion", [])]
        candidates = db.scalars(select(Clip).where(Clip.session_id == sid).order_by(Clip.peak)).all()
        matched = _match(case.events, [c.peak for c in candidates], tolerance)
        clips = [_clip_result(c, matched.get(i)) for i, c in enumerate(candidates)]
        complete = session.scored_until >= session.covered_until - 0.01
    errors = [r for r in jobs if r.get("visual_skipped") or r.get("audio") == "failed"]
    for clip in clips:
        if clip["vlm_fallback"] or not clip["gate"]:
            errors.append({"peak": clip["peak"], "reason": clip["fallback_reason"] or "VLM or gate result missing"})
        confirmation = clip["confirmation"] or {}
        if (confirmation.get("reason") or "").startswith("modelo indisponível"):
            errors.append({"peak": clip["peak"], "reason": confirmation["reason"]})
    if not complete:
        errors.append({"reason": "scoring did not consume all segments"})
    stages = {
        "discovery": [h["peak"] for h in hypotheses],
        "candidates": [c["peak"] for c in clips],
        "targeted_confirmed": [c["peak"] for c in clips if c["targeted_confirmed_in_clip"]],
        "gate_ready": [c["peak"] for c in clips if (c["gate"] or {}).get("outcome") == READY],
    }
    return {"seconds": round(time.monotonic() - t0, 3), "hypotheses": hypotheses, "clips": clips,
            "jobs": jobs, "errors": errors, "metrics": {k: _compare(case.events, v, tolerance) for k, v in stages.items()}}


async def _run(args, cases: list[Case], settings: Settings) -> dict:
    ai = AISettings(vision_model=args.model, whisper_model=args.whisper, visual_discovery="off",
                    multimodal_scoring=True, grounded_metadata=True, gate_enabled=True, director_enabled=True,
                    dataset_enabled=False, editor_mode="off", narrative_model="template")
    manager = get_manager(settings.openvino_device, settings.ai_allow_cpu_fallback)
    _, device = await asyncio.to_thread(manager.acquire, ai.vision_model)
    engine = init_engine(settings.database_url)
    store = SegmentStore(settings.capture_dir)
    capture = CaptureManager(settings, store, ORIGIN)
    app = FastAPI()
    app.include_router(router)
    app.mount("/", create_segment_app(settings, store))
    client = InternalClient(ORIGIN, settings.internal_secret, WORKER, transport=httpx.ASGITransport(app))
    rows = []
    try:
        Base.metadata.create_all(engine)
        await client.post("/internal/workers/register", {"roles": ["audio", "scoring", "vision"],
                          "capabilities": {"gpu": device.startswith("GPU"), "ai_models": [ai.vision_model]}})
        for i, case in enumerate(cases):
            segments = await _prepare(case, settings.work_dir / f"input_{i}", capture)
            modes = {mode: await _replay(case, segments, mode, settings, ai, store, client, args.tolerance)
                     for mode in ("off", "on")}
            rows.append({"video": case.video.name if case.video else case.segments[0].file.name,
                         "negative_control": not any(e.clip_worthy for e in case.events),
                         "events": [e.model_dump() for e in case.events],
                         "segments": [{"start": s.start, "duration": s.duration, "sha256": s.sha256} for s in segments],
                         "modes": modes})
    finally:
        await client.aclose()
        engine.dispose()
        manager.unload()
    summary = {}
    for mode in ("off", "on"):
        summary[mode] = {
            stage: {key: sum(row["modes"][mode]["metrics"][stage][key] for row in rows) for key in ("tp", "fp", "fn")}
            for stage in rows[0]["modes"][mode]["metrics"]
        }
    return {"model": ai.vision_model, "device": device, "gate_version": GATE_VERSION,
            "settings": ai.model_dump(exclude={"visual_discovery"}), "tolerance_s": args.tolerance,
            "summary": summary, "videos": rows}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest", type=Path)
    parser.add_argument("--models-dir", type=Path, required=True)
    parser.add_argument("--model", default=AISettings().vision_model,
                        choices=AISettings.model_fields["vision_model"].annotation.__args__)
    parser.add_argument("--device", choices=["CPU", "GPU"], default="GPU")
    parser.add_argument("--whisper", default="tiny", help="cached Whisper model name or local checkpoint path")
    parser.add_argument("--tolerance", type=float, default=3.0, help="matching window in seconds")
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        manifest = Manifest.model_validate_json(args.manifest.read_text(encoding="utf-8"))
        source_root = args.manifest.resolve().parent
        for case in manifest.videos:
            if case.video is not None:
                case.video = _local_path(source_root, case.video)
            for seg in case.segments:
                seg.file = _local_path(source_root, seg.file)
        inputs = {args.manifest.resolve(), *(c.video for c in manifest.videos if c.video),
                  *(s.file for c in manifest.videos for s in c.segments)}
        if args.out.resolve() in inputs:
            raise ValueError("report path must differ from all inputs")
        if not 0 < args.tolerance < float("inf"):
            raise ValueError("tolerance must be positive and finite")
        with TemporaryDirectory(prefix="asc-replay-") as temp:
            root = Path(temp)
            # A standalone process uses only these local settings, even on a configured worker host.
            for key in tuple(os.environ):
                if key.upper().startswith("ASC_"):
                    del os.environ[key]
            os.environ.update(ASC_ENV="test", ASC_INTERNAL_SECRET=token_hex(32), ASC_API_URL=ORIGIN,
                              ASC_DATABASE_URL=f"sqlite:///{(root / 'replay.db').as_posix()}",
                              ASC_STORAGE_DIR=str(root / "storage"), ASC_CAPTURE_DIR=str(root / "capture"),
                              ASC_WORK_DIR=str(root / "work"), ASC_MODELS_DIR=str(args.models_dir.resolve()),
                              ASC_OPENVINO_DEVICE=args.device, ASC_AI_ALLOW_CPU_FALLBACK="false", HF_HUB_OFFLINE="1")
            reset_settings_cache()
            settings = get_settings()
            settings.work_dir.mkdir()
            report = asyncio.run(_run(args, manifest.videos, settings))
        args.out.write_text(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    except (OSError, RuntimeError, ValueError) as exc:
        parser.exit(2, f"replay: {exc}\n")
    return 2 if any(mode["errors"] for row in report["videos"] for mode in row["modes"].values()) else 0


if __name__ == "__main__":
    raise SystemExit(main())
