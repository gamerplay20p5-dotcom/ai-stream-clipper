"""Second, targeted VLM pass for visual hypotheses: did something meaningful happen, and what?

Separation of concerns (why this exists):
- WHEN comes from the motion detector (analysis/visual_discovery.py), measured on decoded frames at 4 Hz. The VLM is
  never asked to produce a timestamp: small VLMs narrate the shown frames in order ("t": 0.0, 7.0, 8.7 ...) instead
  of locating the change, so their timestamps cannot be trusted as evidence.
- WHETHER and WHAT come from the VLM, asked the same closed question about two pairs of frames:
  * the event pair: one frame just before the hypothesis and one just after it;
  * a control pair: two frames before the hypothesis, where nothing meaningful should change.
  An occurrence is confirmed only when the model sees an event in the event pair AND none in the control pair. A model
  that answers "something happened" to everything (position or yes bias) fails its own control.
Nothing here is ever relaxed to force an approval: an unclear, invalid or inconsistent answer is "not confirmed".
"""

from __future__ import annotations

import time
from dataclasses import asdict, dataclass, field

import numpy as np

CONFIRM_VERSION = "confirm-1"
KINDS = ("rollover", "collision", "fall", "explosion", "none")
PROMPT = ("A imagem 1 é um quadro de um jogo e a imagem 2 é o mesmo jogo cerca de {gap:.1f} segundos depois.\n"
          "Descreva cada imagem e diga se entre elas aconteceu algo marcante no jogo. Responda SOMENTE com JSON: "
          "{{\"image1\": \"até 12 palavras\", \"image2\": \"até 12 palavras\", \"event\": um destes valores: {kinds}, "
          "\"confidence\": 0-1}}\n"
          "Use \"none\" se a cena só continua (direção normal, câmera se mexendo, luzes piscando). Não invente.")
REGEX = (r'\{"image1": "[^"\\\n]{1,90}", "image2": "[^"\\\n]{1,90}", "event": "(' + "|".join(KINDS)
         + r')", "confidence": (0(\.[0-9]{1,2})?|1(\.0)?)\}')
MIN_CONFIDENCE = 0.6


@dataclass
class Confirmation:
    occurred: bool
    kind: str  # one of KINDS minus "none", or "unknown" when not confirmed
    reason: str
    hypothesis: dict  # absolute (session) start/peak/end of the motion hypothesis that was checked
    event_answer: dict = field(default_factory=dict)
    control_answer: dict = field(default_factory=dict)
    frames_at: list[float] = field(default_factory=list)  # absolute times of the four frames shown
    seconds: float = 0.0
    version: str = CONFIRM_VERSION

    def as_dict(self) -> dict:
        return asdict(self)


def frame_times(hypothesis: dict, lo: float, hi: float) -> list[float] | None:
    """Absolute times of [control_a, control_b(=before), after] for a hypothesis, or None when the media around it is
    not available (no frames are invented outside the source)."""
    before = hypothesis["start"] - 0.4
    after = hypothesis["end"] + 0.6
    control_a = before - 1.2
    if control_a < lo or after > hi:
        return None
    return [control_a, before, after]


def _valid(answer: dict) -> bool:
    return (isinstance(answer, dict) and answer.get("event") in KINDS and isinstance(answer.get("confidence"), (int, float))
            and bool(str(answer.get("image1") or "").strip()) and bool(str(answer.get("image2") or "").strip()))


def decide(event: dict, control: dict) -> tuple[bool, str, str]:
    """``(occurred, kind, reason)`` from the two answers. Pure function (tested without a model)."""
    if not _valid(event) or not _valid(control):
        return False, "unknown", "resposta do modelo inválida ou incompleta"
    if control["event"] != "none":
        return False, "unknown", "o modelo também vê um acontecimento no par de controle (resposta não confiável)"
    if event["event"] == "none":
        return False, "unknown", "nenhum acontecimento marcante entre antes e depois da hipótese"
    if float(event["confidence"]) < MIN_CONFIDENCE:
        return False, "unknown", f"confiança baixa ({float(event['confidence']):.2f})"
    return True, event["event"], "acontecimento confirmado entre antes e depois, controle sem mudança"


def confirm(pipe, image_side: int, frames: dict[float, np.ndarray], hypothesis: dict, times: list[float]) -> Confirmation:
    """Run the two questions on an already-acquired VLM pipeline. ``frames`` maps each time in ``times`` to a BGR frame."""
    import openvino_genai as ov_genai

    from .jsonparse import extract_json
    from .vlm import _to_tensor

    def ask(a: float, b: float) -> dict:
        cfg = ov_genai.GenerationConfig()
        cfg.max_new_tokens = 160
        cfg.do_sample = False
        if hasattr(ov_genai, "StructuredOutputConfig"):
            so = ov_genai.StructuredOutputConfig()
            so.regex = REGEX
            cfg.structured_output_config = so
        out = pipe.generate(PROMPT.format(gap=b - a, kinds=", ".join(KINDS)),
                            images=[_to_tensor(frames[a], image_side), _to_tensor(frames[b], image_side)],
                            generation_config=cfg)
        return extract_json(str(out.texts[0] if hasattr(out, "texts") else out)) or {}

    control_a, before, after = times
    t0 = time.monotonic()
    event = ask(before, after)
    control = ask(control_a, before)
    occurred, kind, reason = decide(event, control)
    return Confirmation(occurred, kind, reason, hypothesis, event, control, [round(t, 3) for t in times],
                        round(time.monotonic() - t0, 2))
