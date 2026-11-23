"""Narrative interpretation: editorial justification and title suggestions.

Consumes the *outputs* of the transcript and visual analysis (never raw pixels): a text
model must not pretend to see. Backends:
* ``qwen3-0.6b-int4`` / ``qwen3-1.7b-int4`` - OpenVINO GenAI LLMPipeline
* ``same_as_vision`` - reuse the already loaded VLM in text-only mode (no second model in VRAM)
* ``template`` - Phase-1 local templates (always available)
"""

from __future__ import annotations

import time

import re

from .jsonparse import extract_json
from .manager import ModelManager, ModelUnavailable
from .registry import MODELS
from .schemas import DirectorDecision, NarrativeResult, VisualAnalysis

PROMPT = """Você é editor de Shorts de lives de {game}. Escreva em português do Brasil usando SOMENTE os fatos abaixo.
Fatos verificados:
- O streamer disse: "{transcript}"
- Outras vozes (amigos no rádio, personagens): "{others}"
- O que vinha acontecendo: {story}
- Na tela: {visual}
- Evidências: {evidence}
Regras: fale do ACONTECIMENTO, não da análise (nunca use as palavras "transcrição", "análise", "evidência" ou "streamer");
priorize o que o streamer disse; não invente nada que não esteja nos fatos; títulos com no máximo 60 caracteres;
hashtags curtas, sem # e sem espaços, ligadas ao acontecimento.
Título de editor profissional: concreto (quem + o que aconteceu), prometendo SÓ o que o corte entrega; nunca genérico
("Reação", "Momento", "Conversa", "Gameplay", "Emocional" sozinhos são proibidos); o gancho é a primeira frase que faria alguém parar de rolar o feed.
Exemplo do ESTILO (outro contexto, não copie): "Ela não esperava o gol no último segundo".
Responda apenas com JSON: {{"justification": "por que esse momento prende a atenção, 1-2 frases",
"hook": "frase curta de gancho", "titles": ["título 1", "título 2", "título 3"],
"description": "1-2 frases para a descrição do vídeo", "hashtags": ["tag1", "tag2", "tag3"]}} /no_think"""
PROMPT_VERSION = "narr-2026-11-20-playbook"


META_WORDS = re.compile(r"transcri|an[aá]lise|evid[eê]ncia|json|t[ií]tulo \d", re.I)
TAG_RE = re.compile(r"[^0-9A-Za-zÀ-ÿ_]")


def clean_tags(raw) -> list[str]:
    out: list[str] = []
    for t in raw if isinstance(raw, list) else []:
        t = TAG_RE.sub("", str(t).lstrip("#"))[:30]
        if len(t) >= 3 and not META_WORDS.search(t) and t.lower() not in {x.lower() for x in out}:
            out.append(t)
    return out[:5]


def _facts(transcript: str, visual: VisualAnalysis | None, decision: DirectorDecision, others: str = "",
           story: str = "") -> dict:
    vis = "não disponível"
    if visual is not None and not visual.fallback:
        vis = f"{visual.summary} Destaque: {visual.key_event}".strip()
    evidence = "; ".join(e.detail for e in decision.evidence[:5]) or "nenhuma"
    return {"transcript": " ".join(transcript.split())[:700], "visual": vis[:400], "evidence": evidence[:500],
            "others": " ".join(others.split())[:300] or "nenhuma", "story": (story or "nada registrado")[:400]}


def _desc(raw) -> str:
    d = " ".join(str(raw or "").split())[:400]
    return "" if META_WORDS.search(d) else d


def template_result(fallback_titles: list[str], reasons: list[str]) -> NarrativeResult:
    return NarrativeResult(provider="template", justification=" ".join(reasons[:2])[:600],
                           titles=fallback_titles[:3], fallback=True)


def write(manager: ModelManager | None, backend: str, *, vision_model: str | None, transcript: str,
          visual: VisualAnalysis | None, decision: DirectorDecision, game: str,
          fallback_titles: list[str], others: str = "", story: str = "", cpu_only: bool = False) -> NarrativeResult:
    if backend in ("", "none", "template") or manager is None:
        return template_result(fallback_titles, decision.reasons)
    key = vision_model if backend == "same_as_vision" else backend
    if not key or key not in MODELS:
        return template_result(fallback_titles, decision.reasons)
    prompt = PROMPT.format(game=game or "gameplay", **_facts(transcript, visual, decision, others, story))
    try:
        import openvino_genai as ov_genai

        pipe, device = manager.acquire(key, cpu_only=True) if cpu_only else manager.acquire(key)
        t0 = time.monotonic()  # generation latency only (load time is tracked by the manager)
        cfg = ov_genai.GenerationConfig()
        cfg.max_new_tokens = 320
        cfg.do_sample = False
        cfg.repetition_penalty = 1.1
        if MODELS[key].kind == "vlm":
            out = pipe.generate(prompt, images=[], generation_config=cfg)
        else:
            out = pipe.generate(prompt, cfg)
        texts = getattr(out, "texts", None)
        raw = texts[0] if texts else str(out)
    except ModelUnavailable:
        return template_result(fallback_titles, decision.reasons)
    except Exception:
        manager.stat(key).record(0.0, ok=False)
        return template_result(fallback_titles, decision.reasons)
    latency = time.monotonic() - t0
    obj = extract_json(raw)
    if not obj:
        manager.stat(key).record(latency, ok=False)
        return template_result(fallback_titles, decision.reasons)
    manager.stat(key).record(latency, ok=True)
    titles = obj.get("titles") if isinstance(obj.get("titles"), list) else []
    titles = [t for t in titles if isinstance(t, str) and not META_WORDS.search(t)]
    res = NarrativeResult(provider=f"openvino-llm:{key}@{device}", justification=str(obj.get("justification", ""))[:600],
                          hook=str(obj.get("hook", ""))[:160], titles=[str(t) for t in titles][:5],
                          description=_desc(obj.get("description")), hashtags=clean_tags(obj.get("hashtags")),
                          latency_s=round(latency, 2))
    if not res.titles:
        res.titles = fallback_titles[:3]
    return res
