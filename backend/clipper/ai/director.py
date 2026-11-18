"""Automatic clip director.

Combines transcript timestamps, acoustic reactions (YAMNet + prosody), visual analysis
(multimodal model or heuristics) and the Phase-1 heuristic score to decide whether a
candidate is worth keeping and exactly where it should start and end.

Pure Python (NumPy only): runs in the AI worker after visual analysis, and in the API as
the ``audio_only`` fallback when no AI worker is available. Never invents events: every
adjustment is tied to an :class:`Evidence` item with a timestamp.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from ..analysis import lexicon
from ..scoring.engine import Word, _snap_end, _snap_start
from .schemas import DirectorDecision, Evidence, VisualAnalysis

VOICE_REACTION = ("laughter", "scream", "shout")
EDGE_S = 1.5  # arc points closer than this to the analysed window edges are not informative
MAX_VISUAL_EXTEND_S = 15.0  # how far visual analysis may move the bounds beyond the heuristic candidate
GAME_SOUND = ("game_impact", "gunfire")


@dataclass
class Candidate:
    start: float  # session offsets
    end: float
    peak: float
    score: float
    breakdown: dict = field(default_factory=dict)
    reasons: list[str] = field(default_factory=list)
    kinds: list[str] = field(default_factory=list)


@dataclass
class AcousticTrack:
    """YAMNet category scores and pitch over the analysed window (session offsets)."""

    frames: list[tuple[float, dict[str, float]]] = field(default_factory=list)  # (t_abs, {cat: score})
    pitch: list[tuple[float, float]] = field(default_factory=list)  # (t_abs, hz) voiced only

    @property
    def available(self) -> bool:
        return bool(self.frames)

    def max_in(self, cats: tuple[str, ...], a: float, b: float) -> tuple[float, float | None]:
        best, t_best = 0.0, None
        for t, c in self.frames:
            if a <= t <= b:
                v = max(c.get(k, 0.0) for k in cats)
                if v > best:
                    best, t_best = v, t
        return best, t_best

    def last_above(self, cats: tuple[str, ...], a: float, b: float, thr: float) -> float | None:
        ts = [t for t, c in self.frames if a <= t <= b and max(c.get(k, 0.0) for k in cats) >= thr]
        return max(ts) if ts else None

    def pitch_rise(self, a: float, b: float) -> float:
        """Ratio between median voiced pitch around the event and the window baseline."""
        if not self.pitch:
            return 1.0
        allv = np.array([p for _, p in self.pitch])
        near = np.array([p for t, p in self.pitch if a <= t <= b])
        if len(allv) < 8 or len(near) < 2:
            return 1.0
        return float(np.median(near) / max(1.0, np.median(allv)))


@dataclass
class Limits:
    min_len: float
    max_len: float
    avail_start: float  # media actually available for this clip (session offsets)
    avail_end: float
    min_score: float


def _words(words: list[tuple[float, float, str]]) -> list[Word]:
    return [Word(float(s), float(e), str(w)) for s, e, w in words]


def _motion_at_end(motion: list[tuple[float, float]], t_abs: float) -> tuple[float, float]:
    """(motion around t, median motion). ``motion``: (t_abs, value)."""
    if not motion:
        return 0.0, 0.0
    vals = np.array([m for _, m in motion])
    near = [m for t, m in motion if abs(t - t_abs) <= 1.0]
    return (float(np.mean(near)) if near else 0.0), float(np.median(vals))


def tension_onset(words: list[Word], peak: float, lookback: float = 35.0, max_gap: float = 8.0) -> tuple[float, str] | None:
    """Start of the continuous stretch of tense speech that leads to the peak.

    Walks back from the peak through tension cues (police, chase, "go go go"...) while
    consecutive cues are at most ``max_gap`` apart. Returns ``(sentence_start, cue)``."""
    pre = [w for w in words if peak - lookback <= w.start <= peak]
    if not pre:
        return None
    toks = lexicon.tokenize([w.word for w in pre])
    hits = lexicon.find_tension(toks)
    if not hits:
        return None
    hits.sort(key=lambda h: pre[h[0]].start, reverse=True)
    last = pre[hits[0][0]].start
    if peak - last > 12.0:
        return None  # last cue too far from the peak: not the same situation
    gaps = [b.start - a.end for a, b in zip(pre, pre[1:]) if a.start >= last]
    if gaps and max(gaps) > 4.0:
        return None  # speech stopped between the tension and the peak
    onset_i, cue = hits[0][0], hits[0][1]
    for i, phrase, _w in hits[1:]:
        if pre[onset_i].start - pre[i].start <= max_gap:
            onset_i, cue = i, phrase
        else:
            break
    j = onset_i  # move back to the start of that sentence
    while j > 0 and pre[j - 1].word.rstrip()[-1:] not in ".!?" and pre[j].start - pre[j - 1].end < 0.8:
        j -= 1
    return pre[j].start, cue


def decide(cand: Candidate, words: list[tuple[float, float, str]], acoustic: AcousticTrack,
           visual: VisualAnalysis | None, limits: Limits,
           motion: list[tuple[float, float]] | None = None,
           context_hint: tuple[float, str] | None = None) -> DirectorDecision:
    """``context_hint``: ``(t_abs, why)`` from the narrative model (validated upstream)."""
    ws = _words(words)
    ev: list[Evidence] = []
    adj: list[str] = []
    reasons = list(cand.reasons)
    peak = cand.peak
    start, end = cand.start, cand.end
    mode = "multimodal" if visual is not None and not visual.fallback else "audio_only"

    # ------------------------------------------------------------------ evidence: audio
    voice_reaction, t_voice = (0.0, None)
    game_sound, t_game = (0.0, None)
    pitch_ratio = 1.0
    if acoustic.available:
        voice_reaction, t_voice = acoustic.max_in(VOICE_REACTION, peak - 2.5, peak + 5.0)
        game_sound, t_game = acoustic.max_in(GAME_SOUND, peak - 2.0, peak + 2.0)
        pitch_ratio = acoustic.pitch_rise(peak - 1.0, peak + 4.0)
        if voice_reaction >= 0.3:
            ev.append(Evidence(source="acoustic", t_abs=t_voice,
                               detail=f"Voz do streamer: risada/grito (confiança {voice_reaction:.2f})"))
        if pitch_ratio >= 1.25:
            ev.append(Evidence(source="acoustic", t_abs=peak,
                               detail=f"Tom de voz {pitch_ratio:.2f}x acima do normal da janela"))
        if game_sound >= 0.3:
            ev.append(Evidence(source="acoustic", t_abs=t_game,
                               detail=f"Som do jogo (explosão/tiro) detectado (confiança {game_sound:.2f})"))
    near_words = [w for w in ws if peak - 2.5 <= w.start <= peak + 5.0]
    hits = lexicon.find_hits(lexicon.tokenize([w.word for w in near_words]))
    verbal = sum(h[4] for h in hits)
    if hits:
        ev.append(Evidence(source="transcript", t_abs=near_words[hits[0][0]].start,
                           detail="Fala de reação: " + ", ".join(f"“{h[2]}”" for h in hits[:3])))
    reaction_present = voice_reaction >= 0.3 or verbal >= 2.0 or pitch_ratio >= 1.25 or \
        "scream" in cand.kinds or "laughter" in cand.kinds

    # ------------------------------------------------------------------ evidence: visual
    vis_score = None
    if visual is not None and not visual.fallback:
        w0 = visual.window_abs[0]
        vis_score = visual.interest * (0.5 + 0.5 * visual.confidence)
        if visual.key_event:
            ev.append(Evidence(source="visual", t_abs=w0 + (visual.arc.climax or (peak - w0)),
                               detail=f"Na tela: {visual.key_event}"))
        for e in visual.events[:4]:
            ev.append(Evidence(source="visual", t_abs=w0 + e.t, detail=f"{e.label} (importância {e.importance:.0f})"))

    # ------------------------------------------------------------------ score fusion
    base = float(cand.score)
    score = base
    if vis_score is not None:
        w0 = visual.window_abs[0]
        near_events = [e for e in visual.events if abs(w0 + e.t - peak) <= 3.0 and e.importance >= 5]
        m_peak, m_med = _motion_at_end(motion or [], peak)
        visual_corroborated = bool(near_events) or (m_med > 0 and m_peak >= 1.5 * m_med) or reaction_present
        delta = round(float(np.clip(0.3 * (vis_score * 10 - base), -15, 15)))
        if not visual_corroborated:
            delta = min(0.0, delta)  # an unconfirmed visual opinion may lower, never raise, the score
            adj.append("Opinião visual sem evento confirmado perto do pico: não aumenta a nota")
        score = base + delta
        reasons.append(f"IA visual ({visual.model}): interesse {visual.interest:.0f}/10, ajuste {delta:+.0f} — "
                       f"{visual.summary[:110]}")
        if visual.reaction_linked is False and not reaction_present:
            score -= 12
            adj.append("Penalidade: a reação não tem relação com o que aparece na tela")
        if visual.arc.complete:
            score += 4
            reasons.append("Começo, desenvolvimento e desfecho visíveis")
    if acoustic.available and game_sound >= 0.3 and not reaction_present:
        score -= 25
        adj.append("Penalidade: o pico de volume vem do jogo (explosão/tiro) e o streamer não reagiu")
    elif acoustic.available and voice_reaction >= 0.3:
        score += 6
        reasons.append("Reação de voz confirmada pelo classificador acústico")
    score = float(np.clip(score, 0, 100))
    keep = score >= limits.min_score

    # ------------------------------------------------------------------ boundaries
    must_from, must_to = peak - 1.0, peak + 1.5
    if t_voice is not None and voice_reaction >= 0.3:
        last_voice = acoustic.last_above(VOICE_REACTION, peak - 2.5, peak + 8.0, 0.3) or t_voice
        must_to = max(must_to, last_voice + 1.0)
    if hits:
        must_to = max(must_to, near_words[hits[-1][1]].end + 0.5)
    setup_abs = None
    if visual is not None and not visual.fallback:
        w0, w1 = visual.window_abs
        # Skepticism (measured): small VLMs often answer "the first/last frame" for setup and
        # resolution. Window-edge answers carry no information and low confidence is ignored.
        trusted = visual.confidence >= 0.6

        def informative(t_rel: float | None) -> bool:
            return t_rel is not None and EDGE_S <= t_rel <= (w1 - w0) - EDGE_S

        if visual.preserve and trusted and informative(visual.preserve[0]) and informative(visual.preserve[1]):
            must_from = min(must_from, max(cand.start - MAX_VISUAL_EXTEND_S, w0 + visual.preserve[0]))
            must_to = max(must_to, w0 + visual.preserve[1])
        if trusted and informative(visual.arc.setup_start):
            setup_abs = max(cand.start - MAX_VISUAL_EXTEND_S, w0 + visual.arc.setup_start)
        elif visual.arc.setup_start is not None:
            adj.append("Início sugerido pela IA visual ignorado (borda da janela ou baixa confiança)")
        if trusted and informative(visual.arc.resolution_end):
            res_abs = min(cand.end + MAX_VISUAL_EXTEND_S, w0 + visual.arc.resolution_end)
            if res_abs + 1.0 > end:
                adj.append(f"Fim estendido até o desfecho visual ({res_abs:.1f}s)")
                end = res_abs + 1.0
        elif visual.arc.resolution_end is not None:
            adj.append("Desfecho sugerido pela IA visual ignorado (borda da janela ou baixa confiança)")
    if setup_abs is not None and setup_abs - 1.0 < start:
        adj.append(f"Início antecipado para mostrar o contexto visual ({setup_abs:.1f}s)")
        start = setup_abs - 1.0
    # narrative context: where the situation that leads to the peak begins
    onset = context_hint or tension_onset(ws, peak)
    if onset is not None and onset[0] - 0.5 < start and peak - onset[0] <= limits.max_len - 3:
        src = "narrative" if context_hint else "transcript"
        ev.append(Evidence(source=src, t_abs=round(onset[0], 2), detail=f"A situação começa aqui: “{onset[1][:80]}”"))
        adj.append(f"Início antecipado para incluir o começo da situação ({onset[0]:.1f}s)")
        start = onset[0] - 0.5
        must_from = min(must_from, onset[0] - 0.3)
    if must_to > end:
        adj.append("Fim estendido para manter a reação completa")
        end = must_to
    if must_from < start:
        start = must_from

    # do not end in the middle of the action
    if motion:
        m_end, m_med = _motion_at_end(motion, end)
        extra = 0.0
        while m_med > 0 and m_end > 1.6 * m_med and extra < 4.0 and end + 0.5 <= limits.avail_end:
            end += 0.5
            extra += 0.5
            m_end, _ = _motion_at_end(motion, end)
        if extra:
            adj.append(f"Fim estendido {extra:.1f}s para não cortar no meio da ação")

    # snap to speech boundaries (never cut words), within available media
    lo, hi = limits.avail_start, limits.avail_end
    start, _ = _snap_start(ws, max(lo, start), lo)
    end, _ = _snap_end(ws, min(hi, end), hi)
    start = max(lo, min(start, must_from))
    end = min(hi, max(end, must_to))

    # duration constraints: trim from the side farthest from the protected core
    if end - start > limits.max_len:
        core_from, core_to = max(lo, min(must_from, peak - 1.0)), min(hi, max(must_to, peak + 1.0))
        over = (end - start) - limits.max_len
        cut_front = min(over, max(0.0, core_from - start))
        start += cut_front
        over -= cut_front
        if over > 0:
            end -= min(over, max(0.0, end - core_to))
        adj.append("Duração limitada ao máximo, preservando o acontecimento e a reação")
        start, _ = _snap_start(ws, start, lo)
        start = max(start, end - limits.max_len)
    if end - start < limits.min_len:
        need = limits.min_len - (end - start)
        start = max(lo, start - need * 0.6)
        end = min(hi, start + max(limits.min_len, end - start))

    if not ev:
        ev.append(Evidence(source="heuristic", t_abs=peak, detail="Somente sinais heurísticos de áudio"))
    uniq, seen = [], set()
    for e in ev:  # the same observation reported at several instants is shown once
        key = (e.source, e.detail.lower())
        if key not in seen:
            seen.add(key)
            uniq.append(e)
    ev = uniq
    return DirectorDecision(
        start_abs=round(start, 2), end_abs=round(end, 2), peak_abs=round(peak, 2), score=round(score, 1),
        base_score=round(min(100.0, base), 1), keep=keep, mode=mode, reasons=reasons[:10],
        evidence=ev[:12], adjustments=adj,
    )
