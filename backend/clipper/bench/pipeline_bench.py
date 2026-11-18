"""Pipeline benchmark: Phase 1 (heuristic) vs Phase 2 (acoustic classes + director + vision).

    python -m clipper.bench.pipeline_bench --annotations tests/fixtures/annotations.json \
        --whisper tiny --vlm none|qwen3-vl-2b-int4 --device CPU|GPU --render --out bench.json

Runs every stage in-process on each annotated video (segments of 10 s, like live capture)
and measures real numbers. Nothing is estimated: a metric that was not measured is null.

Detection metrics (per video, against ``annotations.json``):
* TP: a kept clip whose window contains the annotated peak.
* FP: a kept clip that matches no clip-worthy event (e.g. game noise without reaction).
* FN: a clip-worthy event with no matching kept clip.
* coverage: fraction of ``must_include`` covered by the matching clip.
* context_error_s: |clip start - annotated context start| (lower is better).
* reaction_kept: the clip ends after the annotated reaction end.
"""

from __future__ import annotations

import argparse
import json
import platform
import subprocess
import tempfile
import threading
import time
from pathlib import Path

import numpy as np
import psutil

from .. import media
from ..analysis.audio_features import compute_features
from ..settings_store import AISettings

SEG = 10.0


class ResourceSampler:
    """Samples process CPU% and RSS every 0.5 s while a stage runs."""

    def __init__(self):
        self.proc = psutil.Process()
        self.cpu: list[float] = []
        self.rss: list[int] = []
        self._stop = threading.Event()
        self._t = threading.Thread(target=self._run, daemon=True)

    def _run(self):
        self.proc.cpu_percent(None)
        while not self._stop.wait(0.5):
            self.cpu.append(self.proc.cpu_percent(None))
            self.rss.append(self.proc.memory_info().rss)

    def __enter__(self):
        self._t.start()
        return self

    def __exit__(self, *exc):
        self._stop.set()
        self._t.join()

    def summary(self) -> dict:
        return {"cpu_pct_avg": round(float(np.mean(self.cpu)), 1) if self.cpu else None,
                "cpu_pct_max": round(float(np.max(self.cpu)), 1) if self.cpu else None,
                "rss_gb_max": round(max(self.rss) / 1e9, 2) if self.rss else None,
                "logical_cpus": psutil.cpu_count()}


def _pcm(video: Path) -> np.ndarray:
    raw = subprocess.run([media.FFMPEG, "-loglevel", "error", "-i", str(video), "-ac", "1", "-ar", "16000", "-f",
                          "s16le", "-"], capture_output=True, check=True).stdout
    return np.frombuffer(raw, "<i2").astype(np.float32) / 32768.0


def analyse_audio(samples: np.ndarray, whisper: str, language: str, models_dir: Path, use_yamnet: bool) -> dict:
    from ..ai.acoustic import get_classifier, pitch_track
    from ..analysis.transcribe import get_transcriber

    sr = 16000
    tr = get_transcriber(whisper, "int8", 4, None)
    clf = get_classifier(models_dir) if use_yamnet else None
    feats, words = [], []
    t_stt = t_feat = t_yam = 0.0
    for i in range(int(np.ceil(len(samples) / (SEG * sr)))):
        chunk = samples[int(i * SEG * sr):int((i + 1) * SEG * sr)]
        t0 = time.monotonic()
        f = compute_features(chunk, sr)
        extra = {"pitch_hz": pitch_track(chunk), "pitch_hop": 0.25}
        t_feat += time.monotonic() - t0
        if clf is not None:
            t0 = time.monotonic()
            y = clf.classify(chunk)
            extra["yamnet"] = {"hop": y["hop"], "cats": y["cats"]}
            t_yam += time.monotonic() - t0
        feats.append({"start": i * SEG, **f.as_dict(), "extra": extra})
        t0 = time.monotonic()
        r = tr.transcribe(chunk, language)
        t_stt += time.monotonic() - t0
        words += [[round(i * SEG + w.start, 3), round(i * SEG + w.end, 3), w.word, w.probability] for w in r.words]
    return {"features": feats, "words": words, "timings": {"features_s": round(t_feat, 2),
                                                          "transcription_s": round(t_stt, 2),
                                                          "yamnet_s": round(t_yam, 2) if clf else None}}


def evaluate(clips: list[dict], video_ann: dict) -> dict:
    events = [e for e in video_ann.get("events", []) if e.get("clip_worthy", True)]
    matched, out = set(), []
    for e in events:
        hit = next((c for c in clips if c["start"] <= e["peak"] <= c["end"]), None)
        if hit is None:
            out.append({"event": e["label"], "found": False})
            continue
        matched.add(id(hit))
        a, b = e["must_include"]
        cov = max(0.0, min(b, hit["end"]) - max(a, hit["start"])) / max(1e-6, b - a)
        out.append({"event": e["label"], "found": True, "clip": [hit["start"], hit["end"]],
                    "coverage": round(cov, 3),
                    "context_error_s": round(abs(hit["start"] - e["context_start"]), 2),
                    "reaction_kept": hit["end"] >= e["reaction_end"] - 0.3,
                    "peak_error_s": round(abs(hit["peak"] - e["peak"]), 2)})
    fp = [c for c in clips if id(c) not in matched]
    return {"tp": sum(1 for o in out if o["found"]), "fn": sum(1 for o in out if not o["found"]), "fp": len(fp),
            "events": out, "false_positives": [[c["start"], c["end"], c["score"]] for c in fp]}


def run_video(video: Path, ann: dict, args, settings: AISettings) -> dict:
    from ..ai import director, edit_plan
    from ..ai.manager import ModelManager, ModelUnavailable
    from ..ai.vlm import FrameInput, VLMAnalyzer
    from ..analysis.vision import HeuristicVisionProvider, sample_frames
    from ..scoring.engine import score_timeline

    res: dict = {"video": video.name, "duration": ann["duration"]}
    samples = _pcm(video)
    with ResourceSampler() as rs:
        audio = analyse_audio(samples, args.whisper, ann.get("language", "en"), Path(args.models_dir), True)
    res["audio"] = {**audio["timings"], **rs.summary()}
    base_tl = {"from": 0.0, "to": float(ann["duration"]), "data_start": 0.0, "scored_until": 0.0, "final": True,
               "settings": settings.model_dump(), "words": audio["words"], "clips": [],
               "streamer": {"display_name": "bench", "game": "GTA V", "platform": "twitch"}}

    # ---- Phase 1: heuristic only (no acoustic classes, no director) --------------------------
    t0 = time.monotonic()
    old_tl = dict(base_tl, features=[{k: v for k, v in f.items() if k != "extra"} for f in audio["features"]])
    old = score_timeline(old_tl)["candidates"]
    old_clips = [{"start": c["start"], "end": c["end"], "peak": c["peak"], "score": c["score"]} for c in old]
    res["phase1"] = {"scoring_s": round(time.monotonic() - t0, 3), "clips": old_clips, **evaluate(old_clips, ann)}

    # ---- Phase 2: acoustic classes + director (+ vision) -------------------------------------
    t0 = time.monotonic()
    new_tl = dict(base_tl, features=audio["features"])
    cands = score_timeline(new_tl)["candidates"]
    timings = {"scoring_s": round(time.monotonic() - t0, 3), "heuristic_vision_s": 0.0, "vlm_s": 0.0, "director_s": 0.0}
    mgr = ModelManager(preferred_device=args.device, allow_cpu_fallback=args.device != "GPU")
    kept, decisions = [], []
    acoustic_frames, pitch = [], []
    for f in audio["features"]:
        y = f["extra"].get("yamnet") or {}
        for k in range(max((len(v) for v in (y.get("cats") or {}).values()), default=0)):
            acoustic_frames.append((f["start"] + k * y["hop"] + y["hop"], {c: v[k] for c, v in y["cats"].items()}))
        for k, hz in enumerate(f["extra"]["pitch_hz"]):
            if hz:
                pitch.append((f["start"] + k * 0.25, hz))
    track = director.AcousticTrack(acoustic_frames, pitch)
    with ResourceSampler() as rs:
        for c in cands:
            w0 = max(0.0, c["start"] - settings.analysis_context_seconds)
            w1 = min(float(ann["duration"]), c["end"] + settings.analysis_context_seconds)
            t1 = time.monotonic()
            frames = sample_frames(video, w0, w1, c["peak"], 1.0, 90)
            heur = HeuristicVisionProvider().analyze(frames, c["peak"] - w0, c["transcript"])
            timings["heuristic_vision_s"] += time.monotonic() - t1
            visual, vlm_err = None, ""
            if args.vlm != "none":
                t1 = time.monotonic()
                try:
                    words_rel = [(w[0] - w0, w[1] - w0, w[2]) for w in audio["words"] if w0 <= w[0] < w1]
                    visual = VLMAnalyzer(mgr, args.vlm).analyze([FrameInput(f.t, f.image) for f in frames],
                                                                c["peak"] - w0, w1 - w0, (w0, w1), words_rel, [])
                except ModelUnavailable as exc:
                    vlm_err = str(exc)[:200]
                timings["vlm_s"] += time.monotonic() - t1
            t1 = time.monotonic()
            breakdown = dict(c["breakdown"])
            breakdown["visual"] = heur.visual_score
            from ..scoring.engine import weighted_total

            cand = director.Candidate(c["start"], c["end"], c["peak"], weighted_total(breakdown, settings), breakdown,
                                      c["reasons"], c["kinds"])
            motion = [(w0 + m[0], m[1]) for m in heur.extra.get("motion", [])]
            d = director.decide(cand, [w[:3] for w in audio["words"] if w0 <= w[0] < w1], track, visual,
                                director.Limits(settings.min_clip_seconds, settings.max_clip_seconds, 0.0,
                                                float(ann["duration"]), settings.min_score), motion=motion)
            plan = edit_plan.build(d, template_key=settings.edit_template, visual=visual,
                                   motion_cx=[(w0 + m[0], m[2]) for m in heur.extra.get("motion", [])])
            timings["director_s"] += time.monotonic() - t1
            decisions.append({"decision": d.model_dump(include={"start_abs", "end_abs", "peak_abs", "score", "base_score",
                                                                "keep", "mode", "adjustments"}),
                              "layout": plan.layout, "vlm_error": vlm_err,
                              "visual": visual.model_dump(include={"summary", "key_event", "funny", "unexpected",
                                                                   "action", "latency_s", "device"}) if visual else None})
            if d.keep:
                kept.append({"start": d.start_abs, "end": d.end_abs, "peak": d.peak_abs, "score": d.score, "plan": plan})
    res["phase2"] = {**{k: round(v, 2) for k, v in timings.items()}, **rs.summary(), "decisions": decisions,
                     "clips": [{k: v for k, v in c.items() if k != "plan"} for c in kept],
                     **evaluate([{k: v for k, v in c.items() if k != "plan"} for c in kept], ann)}

    # ---- render the first kept clip with its edit plan ------------------------------------------
    if args.render and kept:
        import asyncio

        from ..render.renderer import RenderSpec, render
        from ..render.subtitles import SubWord

        c = kept[0]
        with tempfile.TemporaryDirectory() as td, ResourceSampler() as rs:
            plan = c["plan"].model_dump()
            words = [SubWord(w[0], w[1], w[2]) for w in audio["words"]]
            rr = asyncio.run(render(RenderSpec(source=video, source_start=0.0, start=c["start"], end=c["end"],
                                               peak=c["peak"], words=words, plan=plan, preset=settings.export_preset,
                                               crf=settings.export_crf, threads=4), Path(td)))
            res["render"] = {"render_s": round(rr.seconds, 2), "clip_duration_s": round(rr.duration, 2),
                             "applied": rr.applied, "audio": rr.audio, **rs.summary()}
    return res


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--annotations", default="tests/fixtures/annotations.json")
    ap.add_argument("--whisper", default="tiny")
    ap.add_argument("--vlm", default="none")
    ap.add_argument("--device", default="CPU")
    ap.add_argument("--models-dir", default="data/models")
    ap.add_argument("--render", action="store_true")
    ap.add_argument("--sensitivity", type=float, default=0.5)
    ap.add_argument("--min-score", type=float, default=55.0)
    ap.add_argument("--out", default="")
    args = ap.parse_args(argv)
    ann_path = Path(args.annotations)
    ann = json.loads(ann_path.read_text(encoding="utf-8"))
    settings = AISettings(language="en", sensitivity=args.sensitivity, min_score=args.min_score)
    report = {"sensitivity": args.sensitivity, "min_score": args.min_score, "host": platform.node(), "cpu": platform.processor(), "whisper": args.whisper, "vlm": args.vlm,
              "device": args.device, "videos": []}
    for v in ann["videos"]:
        t0 = time.monotonic()
        r = run_video(ann_path.parent / v["video"], v, args, settings)
        r["total_s"] = round(time.monotonic() - t0, 2)
        report["videos"].append(r)
        print(json.dumps({"video": r["video"], "phase1": {k: r["phase1"][k] for k in ("tp", "fp", "fn", "events")},
                          "phase2": {k: r["phase2"][k] for k in ("tp", "fp", "fn", "events")},
                          "audio": r["audio"], "render": r.get("render"), "total_s": r["total_s"]}, ensure_ascii=False))
    agg = {p: {k: sum(v[p][k] for v in report["videos"]) for k in ("tp", "fp", "fn")} for p in ("phase1", "phase2")}
    report["summary"] = agg
    print(json.dumps({"summary": agg}))
    if args.out:
        Path(args.out).write_text(json.dumps(report, indent=2, ensure_ascii=False, default=str), encoding="utf-8")


if __name__ == "__main__":
    main()
