"""Quality Gate 2.0 (Phase 6, stage A): four separate, explainable dimensions and five outcomes.

Built on top of the existing editorial judgement (``ai/editorial.py``) and story memory - it
regroups their criteria instead of re-scoring, and adds what was missing:

* **event** (A)      - is the HAPPENING good? (interest, humor, surprise, tension, reaction,
                       originality, resolution)
* **narrative** (B)  - is THIS CUT of it good? (context, clarity, standalone, retention, dead time)
* **recovery** (C)   - can editing fix the cut? concrete, applicable fixes (start earlier at the
                       situation start, extend to the outcome, approach a late peak, cut pauses)
* **technical** (D)  - speech/captions/image/duplication/authorisation issues; some are blocking

Outcomes: READY, REWORK (good happening, fixable cut: new edit before any rejection, bounded),
CONTEXT_PENDING (story still unfolding), HUMAN_REVIEW (relevant uncertainty or the two editorial
models disagree), REJECTED (not good enough even after the applicable checks).

Pure function: no DB, no model calls. Scores are 0-10; ``None`` = no evidence (never invented).
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field

READY, REWORK, CONTEXT_PENDING, HUMAN_REVIEW, REJECTED = "READY", "REWORK", "CONTEXT_PENDING", "HUMAN_REVIEW", "REJECTED"
OUTCOME_PT = {READY: "Pronto para revisão", REWORK: "Reeditar", CONTEXT_PENDING: "Aguardando contexto",
              HUMAN_REVIEW: "Revisão humana", REJECTED: "Rejeitado"}
EVENT_KEYS = {"interest": 1.4, "humor": 0.9, "surprise": 0.9, "tension": 0.9, "reaction": 1.0, "originality": 0.6,
              "resolution": 0.8}
NARRATIVE_KEYS = {"context": 1.2, "narrative": 1.2, "standalone": 1.0, "retention": 1.0}
DEAD_GAP_S = 1.5  # silence between words longer than this counts as dead time (when nothing else happens)
VERSION = "gate-2026-11-18a"


@dataclass
class Fix:
    kind: str  # context_before | outcome_after | late_peak | dead_time | framing
    detail: str
    applicable: bool  # can be applied by the current editor (bounds) - dead_time needs the 6B editor
    start: float | None = None
    end: float | None = None


@dataclass
class Dimension:
    score: float | None
    evidence: list[str] = field(default_factory=list)


@dataclass
class GateResult:
    outcome: str
    event: Dimension
    narrative: Dimension
    recovery: Dimension
    technical: Dimension
    fixes: list[Fix]
    reasons: list[str]
    blocking: list[str]
    rework_runs: int
    conflict: bool = False
    version: str = VERSION

    def as_dict(self) -> dict:
        d = asdict(self)
        d["outcome_label"] = OUTCOME_PT[self.outcome]
        return d


@dataclass
class GateSettings:
    event_min: float = 5.5
    narrative_min: float = 5.0
    recovery_min: float = 4.0
    margin: float = 0.7
    max_rework: int = 2
    max_len: float = 90.0


def _mean(criteria: dict, keys: dict) -> tuple[float | None, list[str]]:
    num = den = 0.0
    ev = []
    for k, w in keys.items():
        c = criteria.get(k) or {}
        v = c.get("value")
        if v is None:
            continue
        num += w * float(v)
        den += w
        ev.append(f"{c.get('label', k)} {float(v):.0f}/10")
    return (round(num / den, 1) if den else None), ev


def dead_time(words: list, start: float, end: float) -> tuple[float, float]:
    """(dead seconds, dead ratio) inside [start, end] from the word timings (head and tail included)."""
    ws = sorted((float(w[0]), float(w[1])) for w in words if start <= float(w[0]) < end)
    dur = max(1.0, end - start)
    if not ws:
        return dur, 1.0
    dead = 0.0
    prev = start
    for a, b in ws:
        if a - prev > DEAD_GAP_S:
            dead += a - prev
        prev = max(prev, b)
    if end - prev > DEAD_GAP_S:
        dead += end - prev
    return round(dead, 1), round(dead / dur, 2)


def evaluate(*, judgement: dict, start: float, end: float, peak: float, keep: bool, words: list,
             media: tuple[float, float], kinds: list[str] | None = None, word_probs: list[float] | None = None,
             speakers: list[str] | None = None, visual_ok: bool = False, rework_runs: int = 0,
             history: list[dict] | None = None, settings: GateSettings | None = None,
             is_test: bool = False, approved: bool = False) -> GateResult:
    s = settings or GateSettings()
    crit = judgement.get("criteria") or {}
    story = judgement.get("story") or {}
    kinds = list(kinds or [])
    dur = max(1.0, end - start)
    lo, hi = media

    # A - the happening
    ev_score, ev_ev = _mean(crit, EVENT_KEYS)
    event = Dimension(ev_score, ev_ev[:4] + ([f"situação: {story.get('summary', '')[:100]}"] if story else []))

    # B - this cut of it (dead time measured from the words, a reaction-only moment is not "dead")
    nar_score, nar_ev = _mean(crit, NARRATIVE_KEYS)
    dead_s, dead_r = dead_time(words, start, end) if words is not None else (0.0, 0.0)  # None = not measured
    lively = bool({"laughter", "scream", "game_impact", "loudness_spike"} & set(kinds)) or visual_ok
    if nar_score is not None and dead_r > 0.35 and not lively:
        nar_score = round(max(0.0, nar_score - min(3.0, (dead_r - 0.35) * 8)), 1)
        nar_ev.append(f"{dead_s:.0f}s sem fala de {dur:.0f}s ({dead_r:.0%})")
    narrative = Dimension(nar_score, nar_ev[:5])

    # C - what editing can fix (only fixes whose media still exists are applicable)
    fixes: list[Fix] = []
    s_start, climax, last = story.get("start"), story.get("climax_t"), story.get("last")
    if s_start is not None and float(s_start) < start - 2:
        new_start = max(lo, float(s_start) - 1.0)
        ok = new_start < start - 1 and (end - new_start) <= s.max_len
        fixes.append(Fix("context_before", f"a situação começa {start - float(s_start):.0f}s antes do corte",
                         ok, start=round(new_start, 2) if ok else None))
    outcome_t = climax if climax is not None and float(climax) > end - 1 else (
        last if story.get("outcome") and last is not None and float(last) > end else None)
    if outcome_t is not None and float(outcome_t) > end - 1:
        new_end = min(hi, float(outcome_t) + 4.0)
        ok = new_end > end + 1 and (new_end - start) <= s.max_len
        fixes.append(Fix("outcome_after", f"o clímax/desfecho fica em {float(outcome_t) - start:.0f}s, depois do fim",
                         ok, end=round(new_end, 2) if ok else None))
    if (peak - start) / dur > 0.6 and dur > 20 and not any(f.kind == "context_before" for f in fixes):
        new_start = max(start, peak - max(8.0, 0.35 * dur))
        fixes.append(Fix("late_peak", f"o ponto alto chega em {peak - start:.0f}s de {dur:.0f}s", True,
                         start=round(new_start, 2)))
    if dead_r > 0.25 and not lively:
        fixes.append(Fix("dead_time", f"{dead_s:.0f}s de pausas removíveis", False))  # applied by the 6B editor
    # a cut that misses the start of its situation or its climax is a weaker CUT (not a weaker happening)
    if nar_score is not None:
        for f in fixes:
            pen = {"context_before": 1.5, "outcome_after": 2.0}.get(f.kind, 0.0)
            if pen:
                nar_score = round(max(0.0, nar_score - pen), 1)
                nar_ev.append(f"corte incompleto: {f.detail}")
        narrative = Dimension(nar_score, nar_ev[:6])
    weights = {"context_before": 4.0, "outcome_after": 4.0, "late_peak": 4.0, "dead_time": 2.5, "framing": 1.5}
    rec = min(10.0, sum(weights[f.kind] for f in fixes if f.applicable))
    recovery = Dimension(round(rec, 1) if fixes else 0.0,
                         [f.detail + ("" if f.applicable else " (exige o editor estrutural)") for f in fixes]
                         or ["nenhuma correção de edição identificada"])

    # D - technical and safety
    issues: list[str] = []
    blocking: list[str] = []
    n_words = sum(1 for w in words if start <= float(w[0]) < end) if words is not None else -1
    probs = [p for p in (word_probs or []) if p is not None]
    if probs and sum(probs) / len(probs) < 0.55:
        issues.append(f"transcrição incerta (confiança média {sum(probs) / len(probs):.2f}): revisar legendas")
    if speakers:
        unknown = sum(1 for x in speakers if x in ("unknown", "")) / len(speakers)
        if unknown > 0.6 and len(speakers) >= 8:
            issues.append(f"{unknown:.0%} das palavras sem locutor identificado")
    if n_words == 0 and not lively:
        issues.append("sem fala e sem reação sonora")
    aud = (crit.get("audio") or {}).get("value")
    vis = (crit.get("visual") or {}).get("value")
    red = (crit.get("redundancy") or {}).get("value")
    if red is not None and red < 5:
        issues.append("repete uma situação que já tem corte")
    if dur < 3:
        blocking.append("duração menor que 3 s")
    tech = 10.0 - 1.5 * len(issues) - 5.0 * len(blocking)
    if aud is not None:
        tech = (tech + float(aud)) / 2
    if vis is not None:
        tech = (tech + float(vis)) / 2
    technical = Dimension(round(max(0.0, min(10.0, tech)), 1), issues + blocking or ["sem problemas técnicos detectados"])

    # outcome
    reasons: list[str] = []
    conflict = False
    prev = [h for h in (history or []) if h.get("narrative") is not None]
    stalled = len(prev) >= 1 and nar_score is not None and nar_score < float(prev[-1]["narrative"]) + 0.5
    e, n = ev_score if ev_score is not None else 0.0, nar_score if nar_score is not None else 0.0

    def out(o: str, why: str) -> GateResult:
        reasons.insert(0, why)
        return GateResult(o, event, narrative, recovery, technical, fixes, reasons, blocking, rework_runs, conflict)

    if is_test:
        return out(READY, "corte de teste manual: o gate só informa, não decide")
    if approved:
        return out(READY, "aprovado manualmente: nunca é descartado pelo gate")
    if judgement.get("waiting_context"):
        return out(CONTEXT_PENDING, "a situação ainda está acontecendo: decidir depois do desfecho")
    if blocking:
        return out(REJECTED if e < s.event_min else HUMAN_REVIEW, "problema técnico grave: " + "; ".join(blocking))
    if ev_score is None:  # no editorial evidence about the happening: the director's decision stands
        if keep:
            return out(READY, "sem evidência editorial do acontecimento: mantida a decisão do diretor")
        return out(REJECTED, "sem evidência editorial do acontecimento e o diretor recusou")
    can_rework = rec >= s.recovery_min and any(f.applicable for f in fixes) and rework_runs < s.max_rework
    if e >= s.event_min:
        if n >= s.narrative_min:
            if not keep:
                conflict = True
                return out(HUMAN_REVIEW, "bom acontecimento e bom corte, mas o diretor recusou: decisões em conflito")
            return out(READY, f"acontecimento {e:.1f} e narrativa {n:.1f} acima dos mínimos")
        if can_rework and not (rework_runs and stalled):
            return out(REWORK, f"bom acontecimento ({e:.1f}) com corte fraco ({n:.1f}): nova edição")
        if rework_runs:
            reasons.append(f"{rework_runs} reedição(ões) sem melhora suficiente da narrativa")
        return out(HUMAN_REVIEW, f"bom acontecimento ({e:.1f}), mas a edição não resolve sozinha ({n:.1f})")
    if e >= s.event_min - s.margin:
        if can_rework and not (rework_runs and stalled):
            return out(REWORK, f"acontecimento no limite ({e:.1f}) com correção de edição disponível")
        return out(HUMAN_REVIEW, f"acontecimento no limite ({e:.1f}): incerteza relevante")
    if keep and (judgement.get("final") or 0) >= 70:
        conflict = True
        return out(HUMAN_REVIEW, "o diretor manteve com nota alta, mas o acontecimento ficou fraco: decisões em conflito")
    return out(REJECTED, f"acontecimento fraco ({e:.1f}/10) mesmo considerando as correções possíveis")
