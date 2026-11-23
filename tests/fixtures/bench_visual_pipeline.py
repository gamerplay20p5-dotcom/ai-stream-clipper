"""Compare discovery and scoring on identical media and an identical scorer workload.

Run this same file against either checkout by changing PYTHONPATH. See docs/MULTIMODAL.md.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import statistics
import subprocess
import tempfile
import time
from pathlib import Path

import numpy as np
import psutil

from clipper import media
from clipper.bench.pipeline_bench import ResourceSampler, _pcm, analyse_audio
from clipper.scoring.engine import score_timeline


def scoring_bench(repeat: int, timeline_path: Path, *, visual: bool = False) -> dict:
    src = json.loads(timeline_path.read_text(encoding="utf-8"))
    stride = float(src["to"])
    tl = {**src, "to": 4 * stride, "scored_until": 3 * stride,
          "features": [{**f, "start": f["start"] + stride * i}
                       for i in range(4) for f in src["features"] if i != 1],
          "words": [[a + stride * i, b + stride * i, *rest]
                    for i in range(4) for a, b, *rest in src["words"]]}
    if visual:
        tl["settings"] = {**tl["settings"], "visual_discovery": "on", "multimodal_scoring": True}
        tl["features"].append({"start": 3 * stride, "extra": {"visual_motion": [
            {"start": 20, "peak": 22.5, "end": 24.5, "confidence": 0.72, "signal": "rotation"},
            {"start": 34, "peak": 35, "end": 37.5, "confidence": 0.66, "signal": "vertical_displacement"},
        ]}})
    for _ in range(4):
        score_timeline(tl)
    elapsed = []
    cpu0 = time.process_time()
    with ResourceSampler() as sampler:
        for _ in range(repeat):
            t0 = time.perf_counter()
            result = score_timeline(tl)
            elapsed.append(time.perf_counter() - t0)
    cpu_s = time.process_time() - cpu0
    for candidate in result["candidates"]:
        candidate.pop("visual_evidence", None)
    return {
        "iterations": repeat,
        "median_ms": 1000 * statistics.median(elapsed),
        "p95_ms": 1000 * float(np.percentile(elapsed, 95)),
        "timelines_per_s": repeat / sum(elapsed),
        "cpu_ms_per_timeline": 1000 * cpu_s / repeat,
        "rss_mib_max": max([psutil.Process().memory_info().rss, *sampler.rss]) / 2**20,
        "output_sha256": hashlib.sha256(json.dumps(result, sort_keys=True).encode()).hexdigest(),
    }


def visual_features(video: Path) -> list[dict]:
    from clipper.analysis.visual_discovery import discover_visual

    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        csv_list = root / "segments.csv"
        subprocess.run([media.FFMPEG, "-v", "error", "-i", str(video), "-map", "0:v:0", "-c", "copy",
                        "-f", "segment", "-segment_time", "10", "-reset_timestamps", "1",
                        "-segment_list", str(csv_list), "-segment_list_type", "csv",
                        str(root / "part-%03d.ts")], check=True)
        features = []
        for name, start, end in csv.reader(csv_list.read_text().splitlines()):
            start, end = float(start), float(end)
            evidence = discover_visual(root / name, end - start)
            if evidence:
                features.append({"start": start, "extra": {"visual_motion": evidence}})
        return features


def compare_clips(candidates: list[dict], events: list[dict], k: int = 3) -> dict:
    unmatched = {i for i, e in enumerate(events) if e.get("clip_worthy", True)}
    hits = 0
    coverage = []
    for rank, candidate in enumerate(sorted(candidates, key=lambda c: -c["score"])):
        a, b = candidate["start"], candidate["end"]
        eligible = [i for i in unmatched if a <= events[i]["peak"] <= b
                    and abs(events[i]["peak"] - candidate["peak"]) <= 3]
        if not eligible:
            continue
        best = min(eligible, key=lambda i: abs(events[i]["peak"] - candidate["peak"]))
        unmatched.remove(best)
        x, y = events[best]["must_include"]
        coverage.append(max(0.0, min(y, b) - max(x, a)) / (y - x))
        hits += int(rank < k)
    return {"tp": len(coverage), "fp": len(candidates) - len(coverage), "fn": len(unmatched),
            "precision_at_3": hits / min(k, len(candidates)) if candidates else None,
            "required_coverage": coverage}


def run_media(annotations: Path, mode: str, model_dir: Path) -> dict:
    rows = []
    for ann in json.loads(annotations.read_text(encoding="utf-8"))["videos"]:
        video = annotations.parent / ann["video"]
        language = ann["language"]
        audio = {"features": [], "words": []}
        if media.audio_stream(media.probe_sync(video)) is not None:
            audio = analyse_audio(_pcm(video), "tiny", language, model_dir, True)
        t0 = time.perf_counter()
        visual = visual_features(video) if mode == "on" else []
        elapsed = time.perf_counter() - t0
        settings = {"language": language}
        if mode == "on":
            settings.update(visual_discovery="on", multimodal_scoring=True, grounded_metadata=True)
        result = score_timeline({"from": 0.0, "to": ann["duration"], "final": True, "settings": settings,
                                 "streamer": {"display_name": "Fixture", "registered_game": "GTA V"},
                                 "features": [*audio["features"], *visual], "words": audio["words"]})
        candidates = result["candidates"]
        rows.append({"video": video.name, "duration": ann["duration"], "discovery_s_including_segment_copy": elapsed,
                     "candidates": [{k: c[k] for k in ("start", "end", "peak", "title", "kinds", "score")}
                                    for c in candidates], **compare_clips(candidates, ann["events"])})
    tp, fp, fn = (sum(r[k] for r in rows) for k in ("tp", "fp", "fn"))
    runtime = sum(r["duration"] for r in rows)
    discovery = sum(r["discovery_s_including_segment_copy"] for r in rows)
    return {"tp": tp, "fp": fp, "fn": fn, "recall": tp / (tp + fn) if tp + fn else None,
            "fp_per_hour": fp / (runtime / 3600), "discovery_s_per_live_hour": discovery / (runtime / 3600),
            "videos": rows}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repeat", type=int, default=160)
    parser.add_argument("--mode", choices=["off", "on"], default="off")
    parser.add_argument("--annotations", type=Path, action="append", default=[])
    parser.add_argument("--models-dir", type=Path, default=Path("data/models"))
    parser.add_argument("--out", type=Path)
    args = parser.parse_args()
    here = Path(__file__).parent
    timeline = here / "timeline_event.json"
    report = {"mode": args.mode, "scoring": scoring_bench(args.repeat, timeline)}
    if args.mode == "on":
        report["scoring_with_visual_proposals"] = scoring_bench(args.repeat, timeline, visual=True)
    report["datasets"] = {str(path): run_media(path, args.mode, args.models_dir) for path in args.annotations}
    output = json.dumps(report, indent=2, ensure_ascii=True)
    if args.out:
        args.out.write_text(output + "\n", encoding="utf-8")
    print(output)


if __name__ == "__main__":
    main()
