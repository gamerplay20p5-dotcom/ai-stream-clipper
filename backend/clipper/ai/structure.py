"""Structural editor (Phase 6, stage B): different, FAITHFUL structures for the same happening.

Pure functions (no DB, no models). Times are session offsets (seconds). A structure is a list of
source segments played in order; only two kinds of liberty exist:

* removing pauses (gaps between words) - never inside a protected interval (reactions, the peak,
  the outcome);
* an optional TEASER (2-3 s of the climax shown first) - always labelled on screen ("Daqui a
  pouco..."), and the real sequence then plays in chronological order. Off by default.

Nothing is invented: segments come from the recording, words/reactions are never reordered.

Structures (hypotheses, not rules):
  A  hook       - start close to the action with only the indispensable context
  B  suspense   - a short introduction of the situation, then the build-up, reveal in place
  C  reaction   - a line/joke and the spontaneous reaction, tight pauses
  D  full story - beginning, conflict and conclusion when context is indispensable
  E  quick      - a short moment that explains itself
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field

VERSION = "structure-2026-11-19a"
LABELS = {"A": "Gancho imediato", "B": "Suspense narrativo", "C": "Humor de reação", "D": "História completa",
          "E": "Momento rápido", "O": "Original"}
SENTENCE_GAP = 0.7
TEASER_LABEL = "Daqui a pouco..."
MIN_LEAD_S = 3.0  # structure A: the peak never comes before this much context


@dataclass
class Seg:
    a: float
    b: float
    role: str = "main"  # teaser | intro | main

    @property
    def dur(self) -> float:
        return max(0.0, self.b - self.a)


@dataclass
class Plan:
    key: str
    label: str
    segments: list[Seg]
    applicable: bool = True
    reason: str = ""
    notes: list[str] = field(default_factory=list)
    version: str = VERSION

    @property
    def span(self) -> tuple[float, float]:
        return (min(s.a for s in self.segments), max(s.b for s in self.segments))

    @property
    def duration(self) -> float:
        return round(sum(s.dur for s in self.segments), 2)

    def as_dict(self) -> dict:
        d = asdict(self)
        d.update({"span": list(self.span) if self.segments else None, "duration": self.duration})
        return d


# ------------------------------------------------------------------ helpers
def covered(spans: list[tuple[float, float]], a: float, b: float, tol: float = 0.25) -> bool:
    from .quality_gate import covered as cov

    return cov(spans, a, b, tol) if spans else True


def sentence_start(words: list, t: float, back: float) -> float:
    """Start of the sentence that contains (or precedes) ``t``, looking back at most ``back`` s."""
    ws = sorted((float(w[0]), float(w[1]), str(w[2]) if len(w) > 2 else "") for w in words
                if t - back <= float(w[0]) <= t)
    if not ws:
        return t
    start = ws[-1][0]
    for i in range(len(ws) - 1, 0, -1):
        prev = ws[i - 1]
        if ws[i][0] - prev[1] > SENTENCE_GAP or prev[2][-1:] in ".!?":
            break
        start = ws[i - 1][0]
    return start


def sentence_end(words: list, t: float, fwd: float) -> float:
    ws = sorted((float(w[0]), float(w[1]), str(w[2]) if len(w) > 2 else "") for w in words
                if t <= float(w[0]) <= t + fwd)
    if not ws:
        return t
    end = ws[0][1]
    for i in range(1, len(ws)):
        if ws[i][0] - ws[i - 1][1] > SENTENCE_GAP or ws[i - 1][2][-1:] in ".!?":
            break
        end = ws[i][1]
    return end


def protected_intervals(peak: float, end: float, reactions: list[tuple[float, float]],
                        outcome_t: float | None = None) -> list[tuple[float, float]]:
    """Intervals that pause removal never touches: the peak, every reaction, the outcome, the ending."""
    out = [(peak - 1.5, peak + 3.0), (end - 2.5, end)]
    out += [(a - 0.3, b + 0.8) for a, b in reactions]
    if outcome_t is not None:
        out.append((outcome_t - 1.0, outcome_t + 2.0))
    return sorted(out)


def _inside(t0: float, t1: float, prot: list[tuple[float, float]]) -> bool:
    return any(min(t1, b) - max(t0, a) > 0 for a, b in prot)


def remove_pauses(a: float, b: float, words: list, prot: list[tuple[float, float]], min_gap: float = 0.9,
                  pad: float = 0.25, role: str = "main") -> list[Seg]:
    """Keep [a, b] minus the silent gaps longer than ``min_gap`` (``pad`` kept on both sides),
    except inside protected intervals. Leading/trailing silence is trimmed the same way."""
    ws = sorted((float(w[0]), float(w[1])) for w in words if a <= float(w[0]) < b)
    cuts: list[tuple[float, float]] = []
    prev = a
    for s, e in ws + [(b, b)]:
        gap_a, gap_b = prev + pad, s - pad
        if s - prev > min_gap and gap_b - gap_a >= 0.4 and not _inside(gap_a, gap_b, prot):
            if not (prev == a and s == b):  # never cut everything
                cuts.append((gap_a if prev > a else a, gap_b if s < b else b))
        prev = max(prev, e)
    segs: list[Seg] = []
    pos = a
    for ca, cb in cuts:
        if ca > pos + 0.05:
            segs.append(Seg(round(pos, 2), round(ca, 2), role))
        pos = max(pos, cb)
    if b > pos + 0.05:
        segs.append(Seg(round(pos, 2), round(b, 2), role))
    return segs or [Seg(round(a, 2), round(b, 2), role)]


# ------------------------------------------------------------------ structures
@dataclass
class Moment:
    start: float
    end: float
    peak: float
    words: list
    reactions: list[tuple[float, float]] = field(default_factory=list)
    story: dict | None = None
    media: list[tuple[float, float]] = field(default_factory=list)
    max_len: float = 90.0
    min_gap: float = 0.9
    visual_context: list[tuple[float, float]] = field(default_factory=list)


def _outcome(m: Moment) -> float | None:
    s = m.story or {}
    t = s.get("climax_t")
    return float(t) if t is not None and m.start <= float(t) <= m.end + 0.5 else None


def snap_to_words(segs: list[Seg], words: list) -> list[Seg]:
    """A segment boundary never cuts a word: a word crossing a boundary is kept whole."""
    ws = [(float(w[0]), float(w[1])) for w in words]
    out = []
    for s in segs:
        a, b = s.a, s.b
        for x, y in ws:
            if x < a < y:
                a = round(x - 0.05, 2)
            if x < b < y:
                b = round(y + 0.05, 2)
        out.append(Seg(a, b, s.role))
    merged: list[Seg] = []
    for s in out:  # snapping can make neighbours touch: merge them (same role)
        if merged and s.a <= merged[-1].b + 0.05 and s.role == merged[-1].role:
            merged[-1] = Seg(merged[-1].a, max(merged[-1].b, s.b), s.role)
        else:
            merged.append(s)
    return merged


def _finish(key: str, segs: list[Seg], m: Moment, notes: list[str], teaser: bool) -> Plan:
    segs = snap_to_words([s for s in segs if s.dur >= 0.3], m.words)
    if not segs:
        return Plan(key, LABELS[key], [], False, "nenhum trecho restou")
    for s in segs:
        if not covered(m.media, s.a, s.b):
            return Plan(key, LABELS[key], segs, False, "parte do trecho não está mais gravada")
    if any(not covered([(s.a, s.b) for s in segs], a, b) for a, b in m.visual_context):
        return Plan(key, LABELS[key], segs, False, "variante perderia o contexto ou o desfecho visual")
    total = sum(s.dur for s in segs)
    if total > m.max_len + 0.5:
        return Plan(key, LABELS[key], segs, False, f"ficaria com {total:.0f}s, acima do limite")
    if teaser and key in ("A", "B", "D") and m.peak > segs[0].a + 6:
        t = snap_to_words([Seg(round(m.peak - 0.5, 2), round(min(m.peak + 2.0, m.end), 2), "teaser")], m.words)[0]
        if not covered(m.media, t.a, t.b):
            notes.append("prévia não incluída: trecho não está gravado")
        elif total + t.dur > m.max_len + 0.5:  # the limit applies to the FINAL duration, teaser included
            notes.append(f"prévia não incluída: passaria do limite de {m.max_len:.0f}s")
        else:
            segs = [t] + segs
            notes.append(f"prévia do clímax no início, marcada como '{TEASER_LABEL}'")
    return Plan(key, LABELS[key], segs, True, "", notes)


def build(key: str, m: Moment, *, teaser: bool = False) -> Plan:
    prot = protected_intervals(m.peak, m.end, m.reactions, _outcome(m)) + m.visual_context
    notes: list[str] = []
    s = m.story or {}
    if key == "O":
        return Plan("O", LABELS["O"], [Seg(round(m.start, 2), round(m.end, 2))], True, "", ["corte original"])
    if key == "A":
        a = max(m.start, sentence_start(m.words, m.peak - 1.5, 8.0))
        if m.peak - a < MIN_LEAD_S:  # always at least a few seconds of context, from a sentence start
            a = max(m.start, sentence_start(m.words, m.peak - MIN_LEAD_S, 5.0))
            a = max(m.start, min(a, m.peak - MIN_LEAD_S))
        if a > m.start + 1:
            notes.append(f"começa {a - m.start:.0f}s depois do original, perto da ação")
        return _finish("A", remove_pauses(a, m.end, m.words, prot, m.min_gap), m, notes, teaser)
    if key == "B":
        s0 = s.get("start")
        if s0 is None or float(s0) > m.peak - 15:
            return Plan("B", LABELS["B"], [], False, "sem situação anterior longa o bastante para criar expectativa")
        s0 = float(s0)
        intro_end = min(sentence_end(m.words, s0, 6.0), s0 + 6.0)
        if intro_end - s0 < 1.5:
            intro_end = s0 + 3.0
        if not covered(m.media, s0, intro_end):
            return Plan("B", LABELS["B"], [], False, "o começo da situação não está mais gravado")
        build_a = max(intro_end + 1.0, sentence_start(m.words, m.peak - 8.0, 6.0))
        segs = remove_pauses(s0, intro_end, m.words, prot, m.min_gap, role="intro") + \
            remove_pauses(build_a, m.end, m.words, prot, m.min_gap)
        notes.append(f"apresenta a situação ({intro_end - s0:.0f}s) e pula para a preparação do clímax")
        return _finish("B", segs, m, notes, teaser)
    if key == "C":
        a = max(m.start, sentence_start(m.words, m.peak, 10.0))
        last_reaction = max((b for _, b in m.reactions if b >= m.peak - 1), default=None)
        b = min(m.end, (last_reaction + 1.5) if last_reaction is not None else m.peak + 4.0)
        if last_reaction is None:
            notes.append("sem reação sonora detectada: mantém 4s depois do ponto alto")
        segs = remove_pauses(a, max(b, m.peak + 2.0), m.words, prot, min(m.min_gap, 0.6))
        return _finish("C", segs, m, notes, teaser)
    if key == "D":
        s0 = s.get("start")
        a = m.start if s0 is None else max(float(s0) - 1.0, min(m.start, float(s0) - 1.0))
        if s0 is None:
            notes.append("sem memória da situação: mantém o começo original")
        if not covered(m.media, a, m.end):  # check BEFORE pause removal (it would hide the missing start)
            return Plan("D", LABELS["D"], [], False, "o começo da situação não está mais gravado")
        return _finish("D", remove_pauses(a, m.end, m.words, prot, m.min_gap), m, notes, teaser)
    if key == "E":
        if s.get("kind") in ("police_chase", "shootout", "conflict") and s.get("start") is not None \
                and float(s["start"]) < m.peak - 20:
            return Plan("E", LABELS["E"], [], False, "a situação precisa de contexto: não é um momento rápido")
        a = max(m.start, sentence_start(m.words, m.peak - 1.0, 4.0))
        b = min(m.end, max(m.peak + 4.0, sentence_end(m.words, m.peak, 4.0) + 0.5))
        return _finish("E", [Seg(round(a, 2), round(b, 2))], m, notes, False)
    raise ValueError(f"unknown structure {key}")


def choose(m: Moment, prefs: dict | None = None, kinds: list[str] | None = None) -> tuple[str, str]:
    """Pick a structure for this happening (explainable). ``prefs``: {category: key|'auto'}."""
    s = m.story or {}
    kind = s.get("kind") or ("laughter" if "laughter" in (kinds or []) else "")
    pref = (prefs or {}).get(kind) or (prefs or {}).get("*")
    if pref and pref != "auto":
        return pref, f"preferência configurada para '{kind or 'geral'}'"
    if kind in ("funny_conversation", "laughter") or "laughter" in (kinds or []):
        return "C", "humor: valorizar a fala e a reação"
    if kind in ("police_chase", "shootout", "conflict"):
        s0 = s.get("start")
        if s0 is not None and m.peak - float(s0) > 25:
            return "B", "situação longa: introdução curta e expectativa até o clímax"
        return "D", "situação de tensão: preservar começo, conflito e conclusão"
    if kind == "scare" or (m.end - m.start) <= 18:
        return "E", "acontecimento curto que se explica sozinho"
    return "A", "gancho perto da ação, só o contexto indispensável"


# ------------------------------------------------------------------ mapping and objective metrics
def map_time(segs: list[Seg] | list[dict], t: float) -> float | None:
    """Session time -> output time (None when that moment was removed). For a teaser, the
    chronological (main) occurrence wins, so captions follow the real sequence."""
    ss = [s if isinstance(s, Seg) else Seg(float(s["a"]), float(s["b"]), s.get("role", "main")) for s in segs]
    pos = 0.0
    hit = None
    for s in ss:
        if s.a <= t < s.b:
            if s.role != "teaser":
                return round(pos + (t - s.a), 3)
            hit = round(pos + (t - s.a), 3)
        pos += s.dur
    return hit


def metrics(plan: Plan, m: Moment) -> dict:
    """Objective edit metrics (no model self-score): time to the peak, dead air, reactions kept."""
    from .quality_gate import dead_time

    segs = plan.segments
    if not segs:
        return {}
    out_peak = map_time(segs, m.peak)
    if out_peak is None:  # peak removed should never happen (protected); report it if it does
        out_peak = -1.0
    teaser = any(s.role == "teaser" for s in segs)
    dead = 0.0
    for s in segs:
        if s.role == "teaser":
            continue
        dead += dead_time(m.words, s.a, s.b)[0] if s.dur > 0 else 0.0
    total = plan.duration
    main = [s for s in segs if s.role != "teaser"]

    def kept(t: float) -> bool:
        return any(s.a <= t <= s.b for s in main)

    # reactions: denominators are explicit. "original" = reactions inside the ORIGINAL cut [start, end]
    orig_r = [(a + b) / 2 for a, b in m.reactions if m.start <= (a + b) / 2 <= m.end]
    span_r = [t for t in orig_r if plan.span[0] <= t <= plan.span[1]]
    r_kept = sum(1 for t in orig_r if kept(t))
    r_out_of_bounds = sum(1 for t in orig_r if not (plan.span[0] <= t <= plan.span[1]))  # new start/end
    r_cut_inside = sum(1 for t in span_r if not kept(t))  # removed by pause cutting (should be 0)
    kept_reactions = r_kept == len(orig_r)
    chronological = all(x.a >= y.b - 0.01 for y, x in zip([s for s in segs if s.role != "teaser"],
                                                             [s for s in segs if s.role != "teaser"][1:]))
    words_in = sum(1 for w in m.words if plan.span[0] <= float(w[0]) < plan.span[1])
    words_kept = sum(1 for w in m.words if map_time(segs, float(w[0])) is not None)
    orig_words = [w for w in m.words if m.start <= float(w[0]) < m.end]
    dropped = sum(1 for w in orig_words if map_time(segs, float(w[0])) is None)
    # context loss: speech of the original left out, before the new start and after the new end
    lost = [w for w in m.words if m.start <= float(w[0]) < m.end and map_time(segs, float(w[0])) is None]
    first = min((s.a for s in main), default=m.start)
    last = max((s.b for s in main), default=m.end)
    lost_before = [w for w in lost if float(w[0]) < first]
    lost_after = [w for w in lost if float(w[0]) >= last]
    words_cut = sum(1 for w in m.words for s in segs if float(w[0]) < s.a < float(w[1]) or float(w[0]) < s.b < float(w[1]))
    return {"duration": total, "time_to_peak": round(out_peak, 1), "teaser": teaser,
            "dead_s": round(dead, 1), "dead_ratio": round(dead / total, 2) if total else 0.0,
            "cuts": len(segs) - 1, "reactions_kept": kept_reactions, "chronological": chronological,
            "reactions_original": len(orig_r), "reactions_preserved": r_kept,
            "reactions_dropped_by_bounds": r_out_of_bounds, "reactions_dropped_inside": r_cut_inside,
            "reactions_in_selection": len(span_r), "reactions_preserved_in_selection": len(span_r) - r_cut_inside,
            "words_cut_at_boundaries": words_cut,
            "context_lost_before_s": round(max(0.0, first - m.start), 1) if lost_before else 0.0,
            "context_lost_after_s": round(max(0.0, m.end - last), 1) if lost_after else 0.0,
            "context_lost_text": " ".join(str(w[2]) for w in (lost_before or lost)[:30] if len(w) > 2)[:300],
            "words_kept": words_kept, "words_in_span": words_in,
            # speech of the ORIGINAL cut left out (e.g. context before a hook): shown, never hidden
            "words_dropped_vs_original": dropped, "words_original": len(orig_words)}


def meaningful(orig: dict, new: dict) -> bool:
    """Is the new structure different enough to be worth a second render?"""
    if not orig or not new:
        return False
    return (orig["duration"] - new["duration"] >= 1.5 or abs(orig["time_to_peak"] - new["time_to_peak"]) >= 2.0
            or new["cuts"] >= 2 or new["teaser"])
