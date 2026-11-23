"""Editorial judgement (Phase 5, stage A): explainable criteria on top of the existing signals.

* Every criterion is 0-10 with the evidence that supports it; a criterion without evidence is
  ``None`` (unknown) and does not count - never a made-up middle value.
* VLM opinions are capped: they can move a criterion by at most ``VLM_WEIGHT`` of its range and
  only count when the VLM answer is valid (no fallback).
* ``preliminary`` uses detection-time data; ``final`` adds the visual analysis and the director.
* ``confidence`` measures how much independent evidence exists (modalities, story, validity) - it
  is NOT a probability of going viral.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field

VLM_WEIGHT = 0.35
WEIGHTS = {"interest": 1.4, "narrative": 1.2, "humor": 0.8, "surprise": 0.9, "tension": 0.8, "reaction": 1.1,
           "context": 1.1, "visual": 0.6, "audio": 0.5, "originality": 0.7, "retention": 0.8,
           "standalone": 1.1, "resolution": 1.0, "redundancy": 0.9,
           # Phase 7 playbook: hook/ending describe the CUT (fixable by editing) -> shown, weight 0
           "ingame": 1.2, "payoff": 1.1, "intelligible": 0.7, "hook": 0.0, "ending": 0.0}
LABELS = {"interest": "Interesse do acontecimento", "narrative": "Clareza narrativa", "humor": "Humor",
          "surprise": "Surpresa", "tension": "Tensão e desenvolvimento", "reaction": "Qualidade das reações",
          "context": "Contexto suficiente", "visual": "Qualidade visual", "audio": "Qualidade sonora",
          "originality": "Originalidade", "retention": "Potencial de retenção",
          "standalone": "Compreensível sem conhecer o streamer", "resolution": "Existência de desfecho",
          "redundancy": "Não redundante com outros cortes", "ingame": "Acontece dentro do jogo",
          "payoff": "Tem desfecho/payoff", "intelligible": "Fala compreensível", "hook": "Gancho no início",
          "ending": "Termina no ponto certo"}
STORY_KINDS_TENSE = ("police_chase", "shootout", "conflict")


@dataclass
class Criterion:
    value: float | None
    evidence: list[str] = field(default_factory=list)


@dataclass
class Judgement:
    criteria: dict
    preliminary: float
    final: float | None
    confidence: float
    stage: str  # preliminary | final
    why_selected: str = ""
    best_part: str = ""
    needed_context: str = ""
    weaknesses: list[str] = field(default_factory=list)
    edit_suggestions: list[str] = field(default_factory=list)
    rejection_reason: str = ""
    story: dict | None = None
    waiting_context: bool = False

    def as_dict(self) -> dict:
        d = asdict(self)
        d["criteria"] = {k: {"label": LABELS[k], **asdict(v)} for k, v in self.criteria.items()}
        return d


def _c(v, *ev) -> Criterion:
    return Criterion(None if v is None else round(max(0.0, min(10.0, float(v))), 1), [e for e in ev if e])


def _mmss(t: float) -> str:
    t = max(0, int(t))
    return f"{t // 60}:{t % 60:02d}"


def judge(*, start: float, end: float, peak: float, breakdown: dict, kinds: list[str], story: dict | None = None,
          noise: list[str] | None = None, visual: dict | None = None, director: dict | None = None,
          same_story_clips: int = 0, min_score: float = 55.0, craft=None, fixes: list | None = None,
          visual_grounded: bool = False, evidence_span: tuple[float, float] | None = None) -> Judgement:
    """``story``: situation dict (pipeline_story.story_context()['situation']); ``visual``: VisualAnalysis dict."""
    b = {k: (float(v) if isinstance(v, (int, float)) else None) for k, v in (breakdown or {}).items()}
    kinds = list(kinds or [])
    noise = list(noise or [])
    vis_ok = bool(visual) and not visual.get("fallback")
    grounded = visual_grounded and vis_ok
    visual_complete = False
    if grounded:
        arc = visual.get("arc") or {}
        setup, outcome = arc.get("setup_start"), arc.get("resolution_end")
        w0 = visual["window_abs"][0]
        visual_complete = bool(arc.get("complete") and setup is not None and outcome is not None
                               and start <= w0 + setup < peak < w0 + outcome <= end)
        # Confirmed by the targeted pass: the detector's span with context before and outcome after it is enough
        if evidence_span is not None and start + 1.5 <= evidence_span[0] and evidence_span[1] + 1.0 <= end:
            visual_complete = True
    s = story or None
    dur = max(1.0, end - start)
    c: dict[str, Criterion] = {}

    def vlm(base: float | None, key: str, label: str) -> tuple[float | None, str]:
        if not vis_ok or visual.get(key) is None:
            return base, ""
        v = float(visual[key])
        if base is None:
            return v * VLM_WEIGHT + 5 * (1 - VLM_WEIGHT), f"{label} segundo a IA visual: {v:.0f}/10 (peso limitado)"
        return base + (v - base) * VLM_WEIGHT, f"{label} segundo a IA visual: {v:.0f}/10 (peso limitado)"

    # 1 interest
    story_strength = None
    if s:
        story_strength = 4.0 + (2.5 if s.get("complete") else 0) + (2.0 if s.get("kind") in STORY_KINDS_TENSE else 1.0) \
            + min(1.5, len(s.get("changes") or []) * 0.5)
    base = max([v for v in (b.get("audience"), story_strength) if v is not None], default=None)
    base, ev_v = vlm(base, "important", "Importância")
    if grounded:
        visible_interest = max(visual.get("action", 0), visual.get("unexpected", 0), visual.get("funny", 0))
        base = max(base or 0, visible_interest * visual["confidence"])
        ev_v = f"Acontecimento observado em quadros próximos de {_mmss(peak)}"
    c["interest"] = _c(base, f"situação: {s['summary'][:120]}" if s else "", ev_v,
                       "sinais acústicos e de fala" if b.get("audience") is not None else "")
    # 2 narrative clarity
    narr = b.get("narrative")
    if s:
        narr = (narr or 5.0) + (2.5 if s.get("complete") else (-1.0 if s.get("status") == "closed" else 0))
    if visual_complete:
        narr = max(narr or 0, 7.5)
    c["narrative"] = _c(narr, "começo, desenvolvimento e desfecho observados"
                        if visual_complete or (s and s.get("complete")) else "")
    # 3 humor
    hum, ev_h = vlm(b.get("humor"), "funny", "Humor")
    if grounded and not b.get("humor") and visual.get("funny", 0) < 3:
        hum, ev_h = None, ""
    c["humor"] = _c(hum, "risadas detectadas" if "laughter" in kinds else "", ev_h)
    # 4 surprise
    sur, ev_s = vlm(b.get("surprise"), "unexpected", "Surpresa")
    if grounded:
        sur = max(sur or 0, visual["unexpected"] * visual["confidence"])
    c["surprise"] = _c(sur, "silêncio quebrado" if "silence_break" in kinds else "", ev_s)
    # 5 tension & development
    if s and s.get("kind") in STORY_KINDS_TENSE:
        dev = 5.0 + min(3.0, len(s.get("changes") or [])) + (2.0 if s.get("climax_t") else 0)
        c["tension"] = _c(dev, f"{s['kind']} desde {_mmss(s['start'])}", *(s.get("changes") or [])[:2])
    else:
        c["tension"] = _c(None)
    # 6 reaction
    rea = b.get("reaction")
    if grounded and not rea:
        rea = None
    if noise and rea is not None:
        rea = min(rea, 3.0)
    c["reaction"] = _c(rea, "reação de voz confirmada" if ("scream" in kinds or "laughter" in kinds) else "",
                       *(f"atenção: {n}" for n in noise[:1]))
    # 7 context
    ctx = b.get("context")
    need = ""
    if s and s.get("start") is not None:
        if s["start"] >= start - 2:
            ctx = max(ctx or 0, 7.5)
        elif peak - s["start"] <= dur + 60:
            ctx = min(ctx if ctx is not None else 5, 4.0)
            need = f"a situação começa em {_mmss(s['start'])}, antes do início do corte ({_mmss(start)})"
    if visual_complete:
        ctx = max(ctx or 0, 7.5)
    c["context"] = _c(ctx, "o corte inclui o começo da situação" if s and s.get("start", 1e9) >= start - 2 else "",
                      need)
    # 8 visual quality (only with a valid visual analysis)
    c["visual"] = _c((visual.get("confidence", 0.5) * 10) if vis_ok else None,
                     "análise visual válida" if vis_ok else "")
    # 9 audio quality
    aud = None
    if b.get("intensity") is not None:
        aud = 7.0 if ("speech_burst" in kinds or "keyword" in kinds) else 5.0
        if noise:
            aud -= 2.0
    c["audio"] = _c(aud, "fala clara do streamer" if "keyword" in kinds else "")
    # 10 originality
    c["originality"] = _c(b.get("uniqueness"))
    # 11 retention: the strongest moment should not come too late
    clim = (s or {}).get("climax_t") or peak
    pos = (clim - start) / dur
    c["retention"] = _c(9.0 - 6.0 * max(0.0, pos - 0.35), f"ponto alto em {_mmss(clim - start)} do corte")
    # 12 standalone
    st_v = None
    if s or vis_ok:
        st_v = 4.0 + (3.0 if s and s.get("complete") else 0) + (2.0 if vis_ok and visual.get("summary") else 0) \
            - (2.0 if need else 0)
    c["standalone"] = _c(st_v, "resumo visual disponível" if vis_ok and visual.get("summary") else "")
    # 13 resolution
    res = None
    if s:
        res = 8.5 if s.get("outcome") in ("arrested", "escaped", "laughter") else (3.0 if s.get("status") == "closed"
                                                                                    else None)
    elif visual_complete:
        res = 8.0
        c["payoff"] = _c(8.0, "o desfecho observado fica dentro do corte")
    elif vis_ok and visual.get("arc", {}).get("resolution_end") is not None:
        res = 6.5
    c["resolution"] = _c(res, f"desfecho: {s.get('outcome')}" if s and s.get("outcome") else "")
    # 14 redundancy (10 = not redundant)
    c["redundancy"] = _c(10.0 - 3.0 * same_story_clips,
                         f"{same_story_clips} outro(s) corte(s) da mesma situação" if same_story_clips else "")

    if craft is not None:  # Phase 7 playbook (ai/playbook.py)
        for k, (v, ev) in craft.criteria.items():
            c[k] = _c(v, *ev)
    known = {k: v.value for k, v in c.items() if v.value is not None}
    num = sum(WEIGHTS[k] * v for k, v in known.items())
    den = sum(WEIGHTS[k] for k in known)
    editorial = 10.0 * num / den if den else 0.0
    if noise and not ("laughter" in kinds or "scream" in kinds):
        editorial *= 0.75
    if craft is not None and craft.offgame:
        editorial *= 0.7  # talking about the stream/platform is not a happening
    stage = "final" if (visual is not None or director is not None) else "preliminary"
    final = None
    if stage == "final":
        dir_score = float((director or {}).get("score", editorial))
        final = round(0.5 * editorial + 0.5 * dir_score, 1)

    modalities = 0
    modalities += 1 if b.get("intensity") is not None else 0
    modalities += 1 if ("keyword" in kinds or "laughter" in kinds) else 0
    modalities += 1 if vis_ok else 0
    modalities += 1 if s else 0
    conf = 0.15 + 0.17 * modalities + (0.1 if s and s.get("complete") else 0) + 0.02 * len(known)
    if visual is not None and not vis_ok:
        conf -= 0.1
    conf = round(max(0.05, min(1.0, conf)), 2)

    j = Judgement(criteria=c, preliminary=round(editorial, 1), final=final, confidence=conf, stage=stage, story=s)
    strong = sorted(((k, v) for k, v in known.items()), key=lambda x: -x[1])[:2]
    weak = sorted(((k, v) for k, v in known.items() if v < 5), key=lambda x: x[1])[:3]
    j.why_selected = "; ".join(f"{LABELS[k].lower()} {v:.0f}/10" for k, v in strong) + \
        (f" — {s['summary'][:140]}" if s else "")
    j.best_part = f"em {_mmss(clim - start)} do corte" + (f" ({_mmss(clim)} da live)" if s else "")
    j.needed_context = need or ("o começo da situação já está no corte" if s else "")
    j.weaknesses = [f"{LABELS[k]}: {v:.0f}/10" + (f" — {c[k].evidence[-1]}" if c[k].evidence else "") for k, v in weak]
    if need:
        j.edit_suggestions.append("começar antes, no início da situação")
    if c["resolution"].value is not None and c["resolution"].value < 5:
        j.edit_suggestions.append("estender o fim até o desfecho, se houver")
    if c["retention"].value is not None and c["retention"].value < 5:
        j.edit_suggestions.append("encurtar a preparação: o ponto alto demora a chegar")
    for f in fixes or []:
        if f.get("detail") and f["detail"] not in j.edit_suggestions:
            j.edit_suggestions.append(f["detail"])
    score_for_decision = final if final is not None else editorial
    if craft is not None and craft.offgame:
        j.rejection_reason = "conteúdo fora do jogo: conversa sobre a live ou a plataforma, sem acontecimento"
    elif score_for_decision < min_score:
        j.rejection_reason = "nota editorial abaixo do mínimo" + (f"; principal fraqueza: {j.weaknesses[0]}" if j.weaknesses else "")
    # a story still in progress is never judged final on a low preliminary score
    j.waiting_context = bool(s and s.get("status") == "ongoing" and not s.get("complete"))
    return j
