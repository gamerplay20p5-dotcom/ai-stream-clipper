"""Probe: bias-proof confirmation. The PR's detector proposes WHEN; the VLM is asked the same question about two pairs:
- event pair: one frame just before the hypothesis and one just after it;
- control pair: two frames before the hypothesis (nothing should change).
A confirmation needs a change in the event pair AND no change in the control pair (a model that always says
"changed" fails its own control).

    python tests/fixtures/probe_vlm_contrast.py <models dir> <fixtures dir> [device] [output json]
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "backend"), str(ROOT / "tests")]
import os  # noqa: E402

MODELS, FIXTURES = Path(sys.argv[1]).resolve(), Path(sys.argv[2]).resolve()
DEVICE = sys.argv[3] if len(sys.argv) > 3 else "GPU"
os.environ["ASC_MODELS_DIR"] = str(MODELS)

from clipper.ai.manager import get_manager  # noqa: E402
from clipper.ai.visual_confirm import confirm, frame_times  # noqa: E402
from clipper.ai.vlm import VLMAnalyzer  # noqa: E402
from clipper.analysis.vision import sample_frames  # noqa: E402
from clipper.analysis.visual_discovery import discover_visual  # noqa: E402

manager = get_manager(DEVICE, True)
pipe, device = manager.acquire("qwen3-vl-2b-int4")
side = VLMAnalyzer(manager, "qwen3-vl-2b-int4").spec.image_side


def frame_at(video: Path, t: float):
    return sample_frames(video, t, t + 0.05, t, 1.0, 1)[0].image


# The realistic scenes carry their own ground truth; the synthetic testsrc videos of the repo have no real event and
# must never be confirmed (the detector proposes them because a checkerboard does change abruptly).
entries = json.loads((FIXTURES / "annotations.json").read_text(encoding="utf-8"))["videos"]
entries = [{**e, "path": FIXTURES / e["video"]} for e in entries]
for name in ("chase_crash.mp4", "gameplay_event.mp4", "loud_game_noise.mp4"):
    entries.append({"video": name, "path": ROOT / "tests/fixtures" / name, "events": [], "duration": None})

rows = []
for entry in entries:
    video = entry["path"]
    duration = entry["duration"] or float(__import__("clipper.media", fromlist=["x"]).duration_of(
        __import__("clipper.media", fromlist=["x"]).probe_sync(video)))
    real = bool(entry["events"])
    hyps = discover_visual(video, duration)
    if not hyps:
        rows.append({"video": entry["video"], "real_event": real, "hypotheses": 0, "confirmed": False, "correct": not real})
        print(f"{entry['video']:28s} sem hipótese do detector ({'ERRO: evento perdido' if real else 'OK'})", flush=True)
        continue
    for h in hyps:
        times = frame_times(h, 0.0, duration)
        if times is None:
            rows.append({"video": entry["video"], "hypothesis": h, "confirmed": False, "correct": not real,
                         "reason": "mídia insuficiente"})
            print(f"{entry['video']:28s} pico {h['peak']:5.2f} mídia insuficiente", flush=True)
            continue
        c = confirm(pipe, side, {t: frame_at(video, t) for t in times}, h, times)
        near_truth = real and any(abs(e["t"] - h["peak"]) <= 2.0 for e in entry["events"])
        ok = c.occurred == near_truth
        rows.append({"video": entry["video"], "real_event": real, "correct": ok, **c.as_dict()})
        ev = c.event_answer
        print(f"{entry['video']:28s} pico {h['peak']:5.2f} {h['signal']:15s} confirmado={c.occurred!s:5s} {c.kind:10s} "
              f"{'OK ' if ok else 'ERRO'} {c.seconds:4.1f}s | evento={ev.get('event')} controle={c.control_answer.get('event')}"
              f" | {str(ev.get('image1', ''))[:40]} -> {str(ev.get('image2', ''))[:40]}", flush=True)
out = Path(sys.argv[4]) if len(sys.argv) > 4 else ROOT / "probe-vlm-contrast.json"
out.write_text(json.dumps({"device": device, "rows": rows}, indent=1, ensure_ascii=False, default=float), encoding="utf-8")
