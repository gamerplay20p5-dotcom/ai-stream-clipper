"""Benchmark a vision-language model on the annotated test videos (run on 201/210 and locally).

    python -m clipper.analysis.vlm_bench --model qwen3-vl-2b-int4 --device GPU \
        --annotations tests/fixtures/annotations.json --out bench-vlm.json

For each annotated event, the model sees the same window the AI worker would use
(heuristic start/end +- context) and the result is compared with the ground truth:
does the answer mention a key event, does ``setup_start`` / ``climax`` land near the
annotated context / peak, and how long did it take. Prints JSON; never invents values.
"""

from __future__ import annotations

import argparse
import json
import platform
import time
from pathlib import Path

import psutil


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="qwen3-vl-2b-int4")
    ap.add_argument("--device", default="GPU")
    ap.add_argument("--annotations", default="tests/fixtures/annotations.json")
    ap.add_argument("--context", type=float, default=10.0)
    ap.add_argument("--pre", type=float, default=12.0, help="heuristic pre-context (Phase 1 default)")
    ap.add_argument("--post", type=float, default=8.0)
    ap.add_argument("--repeat", type=int, default=1)
    ap.add_argument("--out", default="")
    a = ap.parse_args(argv)

    from ..ai.manager import ModelManager, ModelUnavailable, gpu_memory
    from ..ai.vlm import FrameInput, VLMAnalyzer
    from .vision import sample_frames

    ann_path = Path(a.annotations)
    ann = json.loads(ann_path.read_text(encoding="utf-8"))
    proc = psutil.Process()
    mgr = ModelManager(preferred_device=a.device, allow_cpu_fallback=False)
    analyzer = VLMAnalyzer(mgr, a.model)
    out = {"model": a.model, "device_requested": a.device, "host": platform.node(), "cpu": platform.processor(),
           "gpu": gpu_memory(), "runs": []}
    for v in ann["videos"]:
        video = ann_path.parent / v["video"]
        targets = v.get("events") or [{"label": d["label"], "peak": d["t"], "clip_worthy": False}
                                      for d in v.get("distractors", [])]
        for ev in targets:
            peak = float(ev["peak"])
            w0 = max(0.0, peak - a.pre - a.context)
            w1 = min(float(v["duration"]), peak + a.post + a.context)
            frames = sample_frames(video, w0, w1, peak, 1.0)
            for _ in range(a.repeat):
                rss0 = proc.memory_info().rss
                t0 = time.monotonic()
                try:
                    r = analyzer.analyze([FrameInput(f.t, f.image) for f in frames], peak - w0, w1 - w0, (w0, w1), [], [])
                    err = ""
                except ModelUnavailable as exc:
                    r, err = None, str(exc)[:300]
                run = {"video": v["video"], "event": ev["label"], "clip_worthy": ev.get("clip_worthy", True),
                       "window": [round(w0, 2), round(w1, 2)], "wall_s": round(time.monotonic() - t0, 2),
                       "rss_gb": round(proc.memory_info().rss / 1e9, 2), "rss_delta_gb": round((proc.memory_info().rss - rss0) / 1e9, 2),
                       "vram": gpu_memory(), "error": err}
                if r is not None:
                    climax_abs = w0 + r.arc.climax if r.arc.climax is not None else None
                    setup_abs = w0 + r.arc.setup_start if r.arc.setup_start is not None else None
                    run.update({
                        "device": r.device, "latency_s": r.latency_s, "summary": r.summary, "key_event": r.key_event,
                        "scores": {"funny": r.funny, "unexpected": r.unexpected, "important": r.important, "action": r.action},
                        "interest": r.interest, "events": [e.model_dump() for e in r.events],
                        "climax_abs": climax_abs, "setup_abs": setup_abs,
                        "climax_error_s": round(abs(climax_abs - peak), 2) if climax_abs is not None else None,
                        "setup_error_s": round(abs(setup_abs - ev["context_start"]), 2)
                        if setup_abs is not None and "context_start" in ev else None,
                        "frames": r.frame_times, "dropped_notes": [n for n in r.notes if not n.startswith("raw:")],
                    })
                out["runs"].append(run)
                print(json.dumps(run, ensure_ascii=False))
    out["model_stats"] = mgr.snapshot()
    if a.out:
        Path(a.out).write_text(json.dumps(out, indent=2, ensure_ascii=False), encoding="utf-8")


if __name__ == "__main__":
    main()
