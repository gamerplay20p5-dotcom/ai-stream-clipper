"""Multimodal analysis of a candidate moment with an OpenVINO vision-language model.

The model sees a short, chronologically ordered sequence of frames (each labelled with its
timestamp), the transcript of the same window and the acoustic cues, and must answer in
JSON. The answer is validated against :class:`VisualAnalysis`; events whose timestamps do
not correspond to the frames that were actually shown are discarded.
"""

from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass

import numpy as np

from .jsonparse import extract_json, salvage_truncated
from .manager import ModelManager, ModelUnavailable
from .registry import MODELS
from .schemas import NarrativeArc, VisualAnalysis, VisualEvent

log = logging.getLogger(__name__)

PROMPT = """Você é editor de cortes curtos (Shorts) de lives de gameplay.
As {n} imagens estão em ordem cronológica e foram capturadas nestes instantes (segundos desde o início da janela de {span:.0f}s): {times}.
Transcrição da fala do streamer na mesma janela (com segundos): {transcript}
Sinais de áudio detectados: {audio}

Responda SOMENTE com um objeto JSON, sem texto extra, no formato:
{{"summary": "o que acontece no gameplay, em português, no máximo 25 palavras",
 "key_event": "o acontecimento que merece atenção, no máximo 12 palavras",
 "funny": 0-10, "unexpected": 0-10, "important": 0-10, "action": 0-10,
 "events": [{{"t": segundos, "label": "no máximo 10 palavras", "importance": 0-10}}] (no máximo 4 eventos, os mais importantes),
 "setup_start": segundos ou null, "climax": segundos ou null, "resolution_end": segundos ou null,
 "complete": true se começo, desenvolvimento e desfecho aparecem,
 "preserve_start": segundos, "preserve_end": segundos,
 "reaction_linked": true se a reação do streamer tem relação com o que aparece na tela,
 "confidence": 0-1}}
Memória da live (contexto anterior, pode estar incompleta): {story}
Critério de editor: acontecimento é algo que MUDA no jogo (perseguição, tiro, queda, susto, prisão, fuga, piada com reação).
Menus, configurações, Discord, navegador, conversa sobre a live/chat/inscrição ou só andar e comentar NÃO são acontecimentos: nesses casos use "important" e "unexpected" baixos.
Use apenas instantes da lista acima. Não invente acontecimentos que não estejam visíveis nas imagens ou na fala."""

# Constrained decoding (OpenVINO GenAI structured output): the answer must match this regex,
# so every text field is bounded and the four scores always come right after the texts. Small
# VLMs otherwise loop inside the first string ("O carro... e o carro... e o carro...") until
# the token limit and never emit the scores. The grammar only shapes syntax and lengths; the
# content is still the model's.
_CH = r'[^"\\\n]'
_N10 = r"(10|[0-9])(\.[0-9])?"
_T = r"[0-9]{1,3}(\.[0-9]{1,2})?"
_TN = rf"({_T}|null)"
_B = "(true|false)"
MAX_SUMMARY, MAX_KEY_EVENT, MAX_LABEL = 300, 160, 140  # well above what the prompt asks for
_EV = rf'\{{"t": {_T}, "label": "{_CH}{{1,{MAX_LABEL}}}", "importance": {_N10}\}}'
ANSWER_REGEX = (
    rf'\{{"summary": "{_CH}{{1,{MAX_SUMMARY}}}", "key_event": "{_CH}{{1,{MAX_KEY_EVENT}}}", '
    rf'"funny": {_N10}, "unexpected": {_N10}, "important": {_N10}, "action": {_N10}, '
    rf'"events": \[({_EV}(, {_EV}){{0,3}})?\], '
    rf'"setup_start": {_TN}, "climax": {_TN}, "resolution_end": {_TN}, "complete": {_B}, '
    rf'"preserve_start": {_T}, "preserve_end": {_T}, "reaction_linked": {_B}, '
    rf'"confidence": (0(\.[0-9]{{1,2}})?|1(\.0)?)\}}'
)

import hashlib as _hashlib

PROMPT_VERSION = "vlm-" + _hashlib.sha1((PROMPT + ANSWER_REGEX).encode()).hexdigest()[:10]

REPAIR = "Sua resposta anterior não era um JSON válido. Responda novamente APENAS com o objeto JSON pedido."


@dataclass
class FrameInput:
    t: float  # seconds relative to the analysed window
    image: np.ndarray  # BGR uint8


def select_frames(frames: list[FrameInput], peak_t: float, max_frames: int) -> list[FrameInput]:
    """Keep the peak neighbourhood dense and the rest evenly spread, chronological order."""
    if len(frames) <= max_frames:
        return list(frames)
    by_peak = sorted(frames, key=lambda f: abs(f.t - peak_t))
    near = by_peak[: max(2, max_frames // 2 - 1)]  # dense around the peak, the rest spread for context
    rest = [f for f in frames if f not in near]
    k = max_frames - len(near)
    if k > 0 and rest:
        idx = np.linspace(0, len(rest) - 1, k).round().astype(int)
        near += [rest[i] for i in sorted(set(idx))]
    return sorted(near[:max_frames], key=lambda f: f.t)


def _to_tensor(img_bgr: np.ndarray, side: int):
    import cv2
    import openvino as ov

    h, w = img_bgr.shape[:2]
    scale = min(1.0, side / max(h, w))
    if scale < 1.0:
        img_bgr = cv2.resize(img_bgr, (int(w * scale) // 2 * 2, int(h * scale) // 2 * 2), interpolation=cv2.INTER_AREA)
    rgb = np.ascontiguousarray(img_bgr[:, :, ::-1])
    return ov.Tensor(rgb[None])


def _fmt_transcript(words: list[tuple[float, float, str]], limit: int = 900) -> str:
    if not words:
        return "(sem fala)"
    parts, cur, t0 = [], [], None
    for s, _e, w in words:
        if t0 is None:
            t0 = s
        cur.append(w)
        if w.rstrip()[-1:] in ".!?" or len(cur) >= 12:
            parts.append(f"[{t0:.1f}s] {' '.join(cur)}")
            cur, t0 = [], None
    if cur:
        parts.append(f"[{t0:.1f}s] {' '.join(cur)}")
    return " ".join(parts)[:limit]


def _num(v, lo=0.0, hi=10.0, default=0.0) -> float:
    try:
        return float(min(hi, max(lo, float(v))))
    except (TypeError, ValueError):
        return default


_SENT = re.compile(r"(?<=[.!?])\s+")


def dedupe_sentences(text: str) -> str:
    """Drop sentences the model repeated (and a trailing fragment that only restarts an
    earlier sentence). Only removes the model's own repeated words; nothing is rewritten."""
    text = " ".join(str(text or "").split())
    kept: list[str] = []
    seen: set[str] = set()
    for sent in _SENT.split(text):
        key = sent.lower().rstrip(".!?,; ")
        if not key or key in seen:
            continue
        if not sent.endswith((".", "!", "?")) and kept and any(k.startswith(key) for k in seen):
            continue  # cut-off restart of a sentence already kept
        seen.add(key)
        kept.append(sent)
    return " ".join(kept).rstrip(",; ")


def drop_cut_word(text: str, cap: int) -> str:
    """A text that reached the grammar's length cap may end in a word cut in half: drop it."""
    if len(text) < cap or " " not in text:
        return text
    return text.rsplit(" ", 1)[0].rstrip(",;: ")


def parse_answer(obj: dict, frame_times: list[float], span: float) -> tuple[dict, list[str]]:
    """Map the raw JSON to VisualAnalysis fields, dropping anything not grounded in the
    timestamps that were shown. Returns ``(fields, notes)``."""
    notes: list[str] = []
    lo, hi = (min(frame_times) - 1.5, max(frame_times) + 1.5) if frame_times else (0.0, span)

    def ts(v):
        if v is None:
            return None
        try:
            f = float(v)
        except (TypeError, ValueError):
            return None
        return f if lo <= f <= hi and 0 <= f <= span + 0.5 else None

    events = []
    for e in obj.get("events") or []:
        if not isinstance(e, dict):
            continue
        t = ts(e.get("t"))
        label = drop_cut_word(str(e.get("label") or "").strip(), MAX_LABEL)
        if t is None or not label:
            notes.append(f"evento descartado (fora dos quadros analisados): {str(e)[:80]}")
            continue
        events.append(VisualEvent(t=t, label=label[:160], importance=_num(e.get("importance"), default=5)))
    setup, climax, res = ts(obj.get("setup_start")), ts(obj.get("climax")), ts(obj.get("resolution_end"))
    pts = [p for p in (setup, climax, res) if p is not None]
    if pts != sorted(pts):
        notes.append("arco narrativo fora de ordem descartado")
        setup = climax = res = None
    ps, pe = ts(obj.get("preserve_start")), ts(obj.get("preserve_end"))
    preserve = (ps, pe) if ps is not None and pe is not None and pe > ps else None
    rl = obj.get("reaction_linked")
    return {
        "summary": dedupe_sentences(drop_cut_word(str(obj.get("summary") or ""), MAX_SUMMARY))[:600],
        "key_event": dedupe_sentences(drop_cut_word(str(obj.get("key_event") or ""), MAX_KEY_EVENT))[:300],
        "funny": _num(obj.get("funny")), "unexpected": _num(obj.get("unexpected")),
        "important": _num(obj.get("important")), "action": _num(obj.get("action")),
        "events": _dedupe_events(events),
        "arc": NarrativeArc(setup_start=setup, climax=climax, resolution_end=res, complete=bool(obj.get("complete"))),
        "preserve": preserve,
        "reaction_linked": rl if isinstance(rl, bool) else None,
        "confidence": _num(obj.get("confidence"), 0, 1, 0.5),
    }, notes


def _dedupe_events(events: list[VisualEvent]) -> list[VisualEvent]:
    """Small VLMs repeat the same sentence for every frame: keep each label once (first time)."""
    seen: set[str] = set()
    out = []
    for e in sorted(events, key=lambda e: e.t):
        key = " ".join(e.label.lower().split())
        if key not in seen:
            seen.add(key)
            out.append(e)
    return out[:12]


def _has_core(obj: dict) -> bool:
    """A usable answer has a description and at least the four scores."""
    return bool(str(obj.get("summary") or "").strip()) and all(k in obj for k in ("funny", "unexpected", "important",
                                                                                   "action"))


class VLMAnalyzer:
    def __init__(self, manager: ModelManager, model_key: str):
        if MODELS.get(model_key) is None or MODELS[model_key].kind != "vlm":
            raise ModelUnavailable(f"{model_key} is not a vision-language model")
        self.manager = manager
        self.key = model_key
        self.spec = MODELS[model_key]

    structured = True  # class-level switch; turned off for the process if the runtime rejects it

    def _generate(self, pipe, prompt: str, tensors, constrained: bool = True) -> str:
        import openvino_genai as ov_genai

        cfg = ov_genai.GenerationConfig()
        cfg.max_new_tokens = self.spec.max_new_tokens
        cfg.do_sample = False
        cfg.repetition_penalty = 1.05
        if constrained and VLMAnalyzer.structured and hasattr(ov_genai, "StructuredOutputConfig"):
            so = ov_genai.StructuredOutputConfig()
            so.regex = ANSWER_REGEX
            cfg.structured_output_config = so
            try:
                out = pipe.generate(prompt, images=tensors, generation_config=cfg)
            except Exception as exc:  # older runtime / backend without grammar support
                log.warning("structured output unavailable, using free generation: %s", exc)
                VLMAnalyzer.structured = False
                return self._generate(pipe, prompt, tensors, constrained=False)
            texts = getattr(out, "texts", None)
            return texts[0] if texts else str(out)
        out = pipe.generate(prompt, images=tensors, generation_config=cfg)
        texts = getattr(out, "texts", None)
        return texts[0] if texts else str(out)

    def analyze(self, frames: list[FrameInput], peak_t: float, span: float, window_abs: tuple[float, float],
                words: list[tuple[float, float, str]], audio_cues: list[str], story: str = "",
                max_frames: int | None = None, setup_t: float | None = None) -> VisualAnalysis:
        pipe, device = self.manager.acquire(self.key)
        st = self.manager.stat(self.key)
        budget = min(16, max_frames or self.spec.max_frames)
        chosen = select_frames(frames, peak_t, budget)
        if setup_t is not None and frames:  # deep review: always show where the situation begins
            first = min(frames, key=lambda f: abs(f.t - setup_t))
            if first not in chosen:
                chosen = sorted(chosen[1:] + [first], key=lambda f: f.t) if len(chosen) >= budget else \
                    sorted(chosen + [first], key=lambda f: f.t)
        times = [round(f.t, 1) for f in chosen]
        prompt = PROMPT.format(n=len(chosen), span=span, times=", ".join(f"{t:.1f}s" for t in times),
                               transcript=_fmt_transcript(words), audio=", ".join(audio_cues) or "nenhum destaque",
                               story=story or "nenhuma situação registrada")
        tensors = [_to_tensor(f.image, self.spec.image_side) for f in chosen]
        t0 = time.monotonic()
        salvaged = False
        try:
            raw = self._generate(pipe, prompt, tensors)
            obj = extract_json(raw)
            if obj is None or not _has_core(obj):
                # the retry is unconstrained, so a grammar-specific failure cannot repeat itself
                raw2 = self._generate(pipe, prompt + "\n\n" + REPAIR, tensors, constrained=False)
                obj = extract_json(raw2) or obj
                if obj is None or not _has_core(obj):
                    # both answers hit the token limit (small VLMs loop on event lists)
                    for cand in (raw, raw2):
                        rec = salvage_truncated(cand)
                        if rec is not None and _has_core(rec):
                            obj, salvaged = rec, True
                            break
                raw = raw + "\n---retry---\n" + raw2
        except Exception as exc:
            st.record(time.monotonic() - t0, ok=False)
            raise ModelUnavailable(f"inference failed on {device}: {type(exc).__name__}: {exc}") from exc
        latency = time.monotonic() - t0
        if obj is None:
            st.record(latency, ok=False)
            raise ModelUnavailable(f"model did not return valid JSON ({len(raw)} chars): "
                                   f"{raw[:160]!r} ... {raw[-160:]!r}")
        if not _has_core(obj):
            st.record(latency, ok=False)
            raise ModelUnavailable(f"incomplete model answer: {raw[-300:]!r}")
        fields, notes = parse_answer(obj, times, span)
        if salvaged:
            notes.append("resposta do modelo truncada no limite de tokens; usados apenas os campos completos")
        st.record(latency, ok=True)
        return VisualAnalysis(
            provider=f"openvino-vlm:{self.key}@{device}", model=self.key, device=device,
            frame_times=times, window_abs=window_abs, latency_s=round(latency, 2), notes=notes + [f"raw:{raw[:400]}"],
            **fields,
        )
