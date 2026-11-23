"""Probe: how does the real VLM report WHEN an event happens? Same frames, three contracts.

A. seconds (current contract): "t": seconds, chosen from a list of instants written in the prompt.
B. image number: "frame": 1..N, mapped to the shown frame's time by the code (never invented).
C. image number with the number drawn on each image.

    python tests/fixtures/probe_vlm_timestamps.py <models dir> [scene] [device] [repeats]
Prints the raw answers and where each contract placed the event relative to the true event time.
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
SCENE = sys.argv[2] if len(sys.argv) > 2 else "rollover"
DEVICE = sys.argv[3] if len(sys.argv) > 3 else "GPU"
REPEATS = int(sys.argv[4]) if len(sys.argv) > 4 else 2
os.environ["ASC_MODELS_DIR"] = str(MODELS)

from fixtures.make_visual_fixtures import write_scene  # noqa: E402

from clipper.ai import vlm  # noqa: E402
from clipper.ai.manager import get_manager  # noqa: E402
from clipper.ai.vlm import FrameInput, VLMAnalyzer, _to_tensor, select_frames  # noqa: E402
from clipper.analysis.vision import sample_frames  # noqa: E402

TRUE_EVENT = {"fall": 13.0, "impact": 13.1, "rollover": 13.25, "distractors": 13.0}[SCENE]  # distractors: no event, any "changed" is a false positive
video = Path(tempfile.mkdtemp()) / f"{SCENE}.mp4"
write_scene(video, SCENE)
W0, W1, PEAK = 3.0, 24.0, TRUE_EVENT + 0.75
frames = [FrameInput(f.t, f.image) for f in sample_frames(video, W0, W1, PEAK, 1.0, 90)]
anchors = (PEAK - W0, TRUE_EVENT - W0 - 1.5, TRUE_EVENT - W0 + 1.5)
chosen = select_frames(frames, PEAK - W0, 8, anchors)
times = [round(f.t, 1) for f in chosen]
truth = TRUE_EVENT - W0
print("quadros mostrados (s desde o início da janela):", times, "| evento real em", round(truth, 2), flush=True)

manager = get_manager(DEVICE, True)
pipe, device = manager.acquire("qwen3-vl-2b-int4")
analyzer = VLMAnalyzer(manager, "qwen3-vl-2b-int4")
base = vlm.PROMPT.format(n=len(chosen), span=W1 - W0, times=", ".join(f"{t:.1f}s" for t in times),
                         transcript="(sem fala)", audio="nenhum destaque", story="nenhuma situação registrada")
FRAME_PROMPT = (
    "As {n} imagens estão numeradas de 1 a {n} em ordem cronológica.\n"
    "Responda SOMENTE com JSON: {{\"summary\": \"até 20 palavras\", \"changed\": true se algo muda de forma marcante no jogo, "
    "\"frame\": número da primeira imagem em que a mudança aparece ou null, \"before_frame\": número de uma imagem antes da "
    "mudança ou null, \"after_frame\": número de uma imagem depois ou null, \"label\": \"até 8 palavras\", \"importance\": 0-10}}\n"
    "Use apenas números de 1 a {n}. Se nada mudar, use changed false e frame null.")


def stamp(img, number: int):
    import cv2
    out = img.copy()
    cv2.rectangle(out, (0, 0), (74, 40), (0, 0, 0), -1)
    cv2.putText(out, str(number), (10, 31), cv2.FONT_HERSHEY_SIMPLEX, 1.1, (255, 255, 255), 2, cv2.LINE_AA)
    return out


def frame_regex(n: int) -> str:
    f = f"([1-{n}]|null)"
    return (r'\{"summary": "[^"\
]{1,140}", "changed": (true|false), "frame": ' + f + ', "before_frame": ' + f
            + ', "after_frame": ' + f + r', "label": "[^"\
]{1,60}", "importance": (10|[0-9])\}')


def run(prompt: str, stamped: bool, regex: str | None = None) -> tuple[str, float]:
    import openvino_genai as ov_genai
    tensors = [_to_tensor(stamp(f.image, i + 1) if stamped else f.image, analyzer.spec.image_side) for i, f in enumerate(chosen)]
    cfg = ov_genai.GenerationConfig()
    cfg.max_new_tokens = 900 if regex is None else 220
    cfg.do_sample = False
    cfg.repetition_penalty = 1.05
    so = ov_genai.StructuredOutputConfig()
    so.regex = regex or vlm.ANSWER_REGEX
    cfg.structured_output_config = so
    t0 = time.monotonic()
    out = pipe.generate(prompt, images=tensors, generation_config=cfg)
    return str(out.texts[0] if hasattr(out, "texts") else out), time.monotonic() - t0


results = []
for variant, prompt, stamped in (("A segundos", base, False), ("B número da imagem", FRAME_PROMPT.format(n=len(chosen)), False),
                                 ("C número estampado", FRAME_PROMPT.format(n=len(chosen)), True)):
    for repeat in range(REPEATS):
        raw, seconds = run(prompt, stamped, None if variant.startswith("A") else frame_regex(len(chosen)))
        obj = vlm.extract_json(raw) or {}
        if variant.startswith("A"):
            ts = [e.get("t") for e in obj.get("events") or [] if isinstance(e, dict)]
            placed = [float(t) for t in ts if isinstance(t, (int, float))]
        else:
            frame = obj.get("frame")
            placed = [times[int(frame) - 1]] if isinstance(frame, int) and 1 <= frame <= len(times) else []
        err = [round(p - truth, 2) for p in placed]
        results.append({"variant": variant, "repeat": repeat, "seconds": round(seconds, 1), "placed": placed, "error_s": err, "raw": raw[:400]})
        print(f"{variant} #{repeat}: {seconds:.1f}s | colocou o evento em {placed} | erro {err} | resposta: {raw[:220]!r}", flush=True)
out = ROOT / f"probe-vlm-{SCENE}.json"
out.write_text(json.dumps({"device": device, "times": times, "truth": truth, "results": results}, indent=1, ensure_ascii=False), encoding="utf-8")
print("salvo em", out)
