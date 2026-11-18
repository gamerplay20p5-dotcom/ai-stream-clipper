"""Benchmark the OpenVINO VLM provider on real frames (run on the GPU hosts).

    python -m clipper.analysis.vlm_bench /models/vlm tests/fixtures/gameplay_event.mp4 [GPU|CPU]

Prints load time, per-call latency, peak RSS and the parsed model answer so the result
can be recorded in docs/VISION_GPU.md. Nothing here is assumed to work until this runs.
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import psutil


def main() -> None:
    model_dir, video = Path(sys.argv[1]), Path(sys.argv[2])
    device = sys.argv[3] if len(sys.argv) > 3 else "GPU"
    from .vision import OpenVINOVLMProvider, sample_frames

    proc = psutil.Process()
    t0 = time.time()
    provider = OpenVINOVLMProvider(model_dir, device)
    load_s = time.time() - t0
    frames = sample_frames(video, 18.0, 30.0, 24.0, 1.0)
    runs = []
    result = None
    for _ in range(3):
        t = time.time()
        result = provider.analyze(frames, 6.0, "Oh my god, no way, no, no, no, what the hell. Okay, that was insane.")
        runs.append(time.time() - t)
    print(json.dumps({
        "model_dir": str(model_dir),
        "device": device,
        "load_seconds": round(load_s, 2),
        "latency_seconds": [round(r, 2) for r in runs],
        "rss_gib": round(proc.memory_info().rss / 1024**3, 2),
        "frames": len(frames),
        "visual_score": result.visual_score if result else None,
        "description": result.description if result else None,
        "raw": (result.extra or {}).get("vlm_raw") if result else None,
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
