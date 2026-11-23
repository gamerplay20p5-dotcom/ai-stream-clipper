"""Probe: focused confirmation pass. The detector gives WHEN (motion hypothesis); the VLM only answers WHETHER
something meaningful changed across the hypothesis and WHAT, on frames tight around it.

Frames: two before the hypothesis (start-1.0, start-0.25) and two after (peak+0.35, end+0.75), in order.
Consistency: a real event must change between image 2 and image 3 (the before/after boundary).

    python tests/fixtures/probe_vlm_focused.py <models dir> [device] [variant]
"""
from __future__ import annotations

import json
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "backend"), str(ROOT / "tests")]
import os  # noqa: E402

MODELS = Path(sys.argv[1]).resolve()
DEVICE = sys.argv[2] if len(sys.argv) > 2 else "GPU"
os.environ["ASC_MODELS_DIR"] = str(MODELS)

from fixtures.make_visual_fixtures import write_scene  # noqa: E402

from clipper.ai.manager import get_manager  # noqa: E402
from clipper.ai.vlm import VLMAnalyzer, _to_tensor, extract_json  # noqa: E402
from clipper.analysis.vision import sample_frames  # noqa: E402

# Hypotheses produced by the PR's detector in the end-to-end run (absolute seconds in the 24 s source)
CASES = {
    "fall": (12.0, 13.75, 14.25, True),
    "impact": (12.75, 13.25, 13.5, True),
    "rollover": (12.0, 14.0, 14.25, True),
    "distractors": (12.0, 13.0, 13.5, False),  # no real event: any confirmation is a false positive
}
KINDS = ["rollover", "collision", "fall", "police_chase", "escape", "failed_escape", "prank", "funny_interaction",
         "surprise", "unlikely_win", "unexpected_failure", "unknown"]
PROMPT = (
    "As 4 imagens são quadros consecutivos de um jogo, em ordem cronológica: as imagens 1 e 2 vêm antes e as imagens 3 e 4 "
    "vêm depois de um instante suspeito.\n"
    "Compare as imagens e responda SOMENTE com JSON: {{\"what\": \"o que muda, até 15 palavras\", "
    "\"changed\": true se acontece algo marcante no jogo (batida, queda, capotamento, explosão), false se for só movimento comum, "
    "\"between\": entre quais imagens a mudança acontece: \"1-2\", \"2-3\", \"3-4\" ou \"none\", "
    "\"event_kind\": um destes valores: {kinds}, \"confidence\": 0-1}}\n"
    "Não invente: se não houver acontecimento claro, use changed false, between \"none\" e event_kind \"unknown\".")
REGEX = (r'\{"what": "[^"\\\n]{1,100}", "changed": (true|false), "between": "(1-2|2-3|3-4|none)", '
         r'"event_kind": "(' + "|".join(KINDS) + r')", "confidence": (0(\.[0-9]{1,2})?|1(\.0)?)\}')

manager = get_manager(DEVICE, True)
pipe, device = manager.acquire("qwen3-vl-2b-int4")
side = VLMAnalyzer(manager, "qwen3-vl-2b-int4").spec.image_side
tmp = Path(tempfile.mkdtemp())
rows = []
import openvino_genai as ov_genai  # noqa: E402

for scene, (start, peak, end, real) in CASES.items():
    video = tmp / f"{scene}.mp4"
    write_scene(video, scene)
    wanted = [start - 1.0, start - 0.25, peak + 0.35, end + 0.75]
    frames = []
    for t in wanted:
        got = sample_frames(video, t, t + 0.05, t, 1.0, 1)
        frames.append(got[0].image)
    cfg = ov_genai.GenerationConfig()
    cfg.max_new_tokens = 160
    cfg.do_sample = False
    so = ov_genai.StructuredOutputConfig()
    so.regex = REGEX
    cfg.structured_output_config = so
    t0 = time.monotonic()
    out = pipe.generate(PROMPT.format(kinds=", ".join(KINDS)), images=[_to_tensor(f, side) for f in frames], generation_config=cfg)
    raw = str(out.texts[0] if hasattr(out, "texts") else out)
    obj = extract_json(raw) or {}
    confirmed = bool(obj.get("changed")) and obj.get("between") == "2-3"
    rows.append({"scene": scene, "real_event": real, "seconds": round(time.monotonic() - t0, 1), "frames_at": wanted,
                 "answer": obj, "confirmed": confirmed, "correct": confirmed == real})
    print(f"{scene:12s} real={real!s:5s} confirmado={confirmed!s:5s} {'OK ' if confirmed == real else 'ERRO'} "
          f"{rows[-1]['seconds']}s | {raw[:200]}", flush=True)
(ROOT / "probe-vlm-focused.json").write_text(json.dumps({"device": device, "rows": rows}, indent=1, ensure_ascii=False), encoding="utf-8")
