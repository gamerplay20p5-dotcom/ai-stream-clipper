"""Clip scoring engine (layer 1 event detection + layer 3 scoring).

Pure and deterministic: input is a *timeline* (acoustic features, transcript words,
existing clips, settings) and output is a list of events and clip candidates with a
score breakdown and short human-readable explanations (PT-BR).

Scores are **heuristics**, not calibrated probabilities of virality.
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field

import numpy as np

from ..analysis import lexicon
from ..settings_store import AISettings
from .titles import suggest_metadata

# Bump whenever detection/scoring logic changes: stored with every candidate and clip so
# evaluations can be reproduced and compared across versions.
SCORING_VERSION = "score-3.0"

HOP = 0.25
ABS_FLOOR_DB = -48.0  # ignore "spikes" in near-silence
BASE_WINDOW_S = 90.0
BASE_GUARD_S = 2.0
CLUSTER_GAP_S = 6.0


@dataclass
class Word:
    start: float
    end: float
    word: str
    prob: float = 1.0


@dataclass
class Ev:
    t: float
    duration: float
    kind: str
    strength: float
    data: dict = field(default_factory=dict)


@dataclass
class Candidate:
    start: float
    end: float
    peak: float
    score: float
    breakdown: dict
    reasons: list[str]
    transcript: str
    kinds: list[str]
    title: str = ""
    title_suggestions: list[str] = field(default_factory=list)
    description: str = ""
    hashtags: list[str] = field(default_factory=list)
    features: dict = field(default_factory=dict)  # observable signals (ranker / dataset)


ACOUSTIC_CATS = ("speech", "laughter", "scream", "shout", "game_impact", "gunfire", "vehicle", "music", "crowd")


@dataclass
class Grid:
    t0: float
    loud: np.ndarray
    cent: np.ndarray
    flux: np.ndarray
    cats: dict = field(default_factory=dict)  # YAMNet category -> array aligned to HOP (NaN = unknown)

    def cat(self, name: str, a: int, b: int) -> float:
        arr = self.cats.get(name)
        if arr is None:
            return float("nan")
        seg = arr[max(0, a):max(a + 1, b + 1)]
        seg = seg[~np.isnan(seg)]
        return float(seg.max()) if len(seg) else float("nan")

    @property
    def has_cats(self) -> bool:
        return any(np.isfinite(v).any() for v in self.cats.values())

    def t(self, i: int) -> float:
        return self.t0 + i * HOP

    def i(self, t: float) -> int:
        return int(round((t - self.t0) / HOP))

    @property
    def n(self) -> int:
        return len(self.loud)


# ---------------------------------------------------------------------------
# timeline assembly
# ---------------------------------------------------------------------------


def build_grid(features: list[dict], t_from: float, t_to: float) -> Grid:
    n = max(0, int(math.ceil((t_to - t_from) / HOP)))
    loud = np.full(n, np.nan)
    cent = np.full(n, np.nan)
    flux = np.full(n, np.nan)
    cats = {c: np.full(n, np.nan) for c in ACOUSTIC_CATS}
    for f in features:
        y = ((f.get("extra") or {}).get("yamnet")) or {}
        if y.get("cats"):
            yhop = float(y.get("hop", 0.48))
            for c, vals in y["cats"].items():
                if c not in cats:
                    continue
                for k, v in enumerate(vals):
                    a = int(round((float(f["start"]) + k * yhop - t_from) / HOP))
                    b = int(round((float(f["start"]) + (k + 2) * yhop - t_from) / HOP))  # 0.96 s window
                    for idx in range(max(0, a), min(n, b)):
                        cats[c][idx] = v if np.isnan(cats[c][idx]) else max(cats[c][idx], v)
        hop = float(f.get("hop", HOP)) or HOP
        ld = np.asarray(f.get("loudness_db") or [], dtype=float)
        cd = np.asarray(f.get("centroid_hz") or [], dtype=float)
        fx = np.asarray(f.get("flux") or [], dtype=float)
        for k in range(len(ld)):
            idx = int(round((float(f["start"]) + k * hop - t_from) / HOP))
            if 0 <= idx < n:
                loud[idx] = ld[k]
                cent[idx] = cd[k] if k < len(cd) else np.nan
                flux[idx] = fx[k] if k < len(fx) else np.nan
    return Grid(t_from, loud, cent, flux, cats)


def rolling_baseline(x: np.ndarray, window: int, guard: int) -> tuple[np.ndarray, np.ndarray]:
    """Causal rolling median / robust spread (MAD) excluding the most recent ``guard`` bins."""
    n = len(x)
    base = np.full(n, np.nan)
    spread = np.full(n, np.nan)
    valid_all = x[~np.isnan(x)]
    g_med = float(np.median(valid_all)) if len(valid_all) else -60.0
    g_mad = float(np.median(np.abs(valid_all - g_med))) if len(valid_all) else 3.0
    for i in range(n):
        lo, hi = max(0, i - window), max(0, i - guard)
        w = x[lo:hi]
        w = w[~np.isnan(w)]
        if len(w) >= 40:
            med = float(np.median(w))
            mad = float(np.median(np.abs(w - med)))
        else:
            med, mad = g_med, g_mad
        base[i] = med
        spread[i] = max(3.0, 1.4826 * mad)
    return base, spread


# ---------------------------------------------------------------------------
# event detection
# ---------------------------------------------------------------------------


def _runs(mask: np.ndarray, max_gap: int = 2) -> list[tuple[int, int]]:
    runs: list[tuple[int, int]] = []
    start = None
    last = None
    for i, m in enumerate(mask):
        if m:
            if start is None:
                start = i
            elif last is not None and i - last - 1 > max_gap:  # number of missing bins
                runs.append((start, last))
                start = i
            last = i
    if start is not None and last is not None:
        runs.append((start, last))
    return runs


def detect_events(grid: Grid, words: list[Word], region: tuple[float, float], sensitivity: float) -> list[Ev]:
    events: list[Ev] = []
    if grid.n == 0:
        return events
    base, spread = rolling_baseline(grid.loud, int(BASE_WINDOW_S / HOP), int(BASE_GUARD_S / HOP))
    z = (grid.loud - base) / spread
    zthr = 3.6 - 2.0 * sensitivity  # 3.6 (conservative) .. 1.6 (aggressive)
    loud_ok = np.nan_to_num(grid.loud, nan=-120) > ABS_FLOOR_DB
    mask = (np.nan_to_num(z, nan=0) >= zthr) & loud_ok
    cent_valid = grid.cent[~np.isnan(grid.cent)]
    cent_med = float(np.median(cent_valid)) if len(cent_valid) else 0.0
    r0, r1 = region
    rel_db = np.nan_to_num(grid.loud - base, nan=-99)

    # bright bursts: harsh / high-pitched sound clearly above the baseline (screams, shrieks)
    bright: list[tuple[float, float]] = []
    if cent_med > 0:
        bmask = (np.nan_to_num(grid.cent, nan=0) > 2.5 * cent_med) & (rel_db > 3.0) & loud_ok
        for a, b in _runs(bmask, max_gap=1):
            dur = (b - a + 1) * HOP
            if dur < 0.75:
                continue
            t_a, t_b = grid.t(a), grid.t(b) + HOP
            bright.append((t_a, t_b))
            if r0 <= t_a < r1:
                ratio = float(np.nanmean(grid.cent[a:b + 1]) / cent_med)
                events.append(Ev(round(t_a, 2), round(dur, 2), "bright_burst",
                                 round(min(10.0, float(rel_db[a:b + 1].max()) / 2 + ratio / 3), 2),
                                 {"centroid_ratio": round(ratio, 1)}))

    for a, b in _runs(mask):
        t_a, t_b = grid.t(a), grid.t(b) + HOP
        seg_z = np.nan_to_num(z[a:b + 1], nan=0)
        k = int(np.argmax(seg_z))
        peak_t = grid.t(a + k)
        if not (r0 <= peak_t < r1):
            continue
        dur = t_b - t_a
        zmax = float(seg_z[k])
        if dur < 0.5 and zmax < zthr + 2.0:
            continue
        delta_db = float(np.nanmax(grid.loud[a:b + 1] - base[a:b + 1]))
        # rise rate: change over the 1 s before the run
        pre_i = max(0, a - 4)
        pre_level = np.nanmean(grid.loud[pre_i:a]) if a > pre_i else np.nan
        rise = float(np.nanmax(grid.loud[a:b + 1]) - pre_level) if not np.isnan(pre_level) else delta_db
        source = "unknown"
        if grid.has_cats:
            voice_react = max(grid.cat("laughter", a, b), grid.cat("scream", a, b), grid.cat("shout", a, b))
            speech = grid.cat("speech", a, b)
            game = max(grid.cat("game_impact", a, b), grid.cat("gunfire", a, b))
            voice = voice_react >= 0.3 or speech >= 0.5
            gamey = game >= 0.3
            source = "mixed" if voice and gamey else "voice" if voice else "game" if gamey else "other"
        events.append(Ev(round(peak_t, 2), round(dur, 2), "loudness_spike", round(zmax, 2),
                         {"delta_db": round(delta_db, 1), "rise_db": round(rise, 1), "source": source}))
        # scream: loud and bright, or a bright burst immediately before the loud reaction
        seg_c = grid.cent[a:b + 1]
        bright_spike = cent_med > 0 and np.nanmean(seg_c) > 1.35 * cent_med
        bright_before = any(t_a - 1.5 <= bb <= t_a + 0.5 for _, bb in bright)
        if delta_db >= 12 and dur >= 0.5 and (bright_spike or bright_before):
            events.append(Ev(round(peak_t, 2), round(dur, 2), "scream", round(min(10.0, delta_db / 2.5), 2),
                             {"bright_spike": bool(bright_spike), "bright_before": bool(bright_before)}))
        # silence -> sudden event: a continuous quiet stretch >= 1.5 s within the 4 s before
        # the event (or before the bright burst that leads into it)
        lead = min([bs for bs, bb in bright if t_a - 1.5 <= bb <= t_a + 0.5] + [t_a])
        q1 = grid.i(lead)
        q0 = max(0, q1 - int(4.0 / HOP))
        quiet_mask = np.nan_to_num(grid.loud[q0:q1], nan=-120) < (base[a] - 8.0)
        qruns = _runs(quiet_mask, max_gap=0)
        best = max(qruns, key=lambda r: r[1] - r[0], default=None)
        longest = (best[1] - best[0] + 1) * HOP if best else 0.0
        if best and longest >= 1.5:
            q_t0, q_t1 = grid.t(q0 + best[0]), grid.t(q0 + best[1]) + HOP
            words_in_quiet = sum(1 for w in words if w.start < q_t1 and w.end > q_t0 + 0.2)
            if words_in_quiet <= 1:
                events.append(Ev(round(lead, 2), 0.0, "silence_break", round(min(10.0, longest * 3), 2),
                                 {"quiet_seconds": round(longest, 2)}))

    # classifier-based voice reactions (YAMNet): laughter and screams/shouts
    if grid.has_cats:
        for name, cats_, kind in (("laughter", ("laughter",), "laughter"), ("scream", ("scream", "shout"), "scream")):
            arr = np.nanmax(np.vstack([np.nan_to_num(grid.cats[c], nan=0.0) for c in cats_]), axis=0)
            for a, b in _runs(arr >= 0.4, max_gap=1):
                dur = (b - a + 1) * HOP
                t_a = grid.t(a)
                if dur >= 0.5 and r0 <= t_a < r1:
                    events.append(Ev(round(t_a, 2), round(dur, 2), kind, round(float(arr[a:b + 1].max()) * 10, 2),
                                     {"by": "yamnet", "label": name}))

    # sustained modulated energy (laughter / excited burst) via flux
    if np.isfinite(grid.flux).any():
        fvalid = grid.flux[~np.isnan(grid.flux)]
        fthr = float(np.percentile(fvalid, 95)) if len(fvalid) > 50 else float("inf")
        fmask = (np.nan_to_num(grid.flux, nan=0) >= max(fthr, 1.5)) & loud_ok
        for a, b in _runs(fmask, max_gap=1):
            dur = (b - a + 1) * HOP
            t_mid = grid.t((a + b) // 2)
            if dur >= 1.5 and r0 <= t_mid < r1:
                events.append(Ev(round(t_mid, 2), round(dur, 2), "energy_burst", round(min(10.0, dur * 2), 2), {}))

    # lexicon / laughter (text layer)
    tokens = lexicon.tokenize([w.word for w in words])
    for i0, i1, phrase, cat, weight in lexicon.find_hits(tokens):
        t = words[i0].start
        if r0 <= t < r1:
            kind = "laughter" if cat == "humor" else "keyword"
            events.append(Ev(round(t, 2), round(words[i1].end - t, 2), kind, weight,
                             {"phrase": phrase, "category": cat}))

    # speech-rate burst
    if len(words) >= 8:
        starts = np.array([w.start for w in words])
        total_span = max(1.0, starts[-1] - starts[0])
        med_rate = len(words) / total_span
        for i in range(len(words)):
            t = words[i].start
            if not (r0 <= t < r1):
                continue
            cnt = int(np.sum((starts >= t) & (starts < t + 3.0)))
            rate = cnt / 3.0
            if rate >= 3.5 and rate >= 1.8 * med_rate:
                events.append(Ev(round(t, 2), 3.0, "speech_burst", round(rate, 2), {"wps": round(rate, 2)}))
    # collapse duplicated speech bursts (one per 3 s)
    events.sort(key=lambda e: e.t)
    out: list[Ev] = []
    last_burst = -1e9
    for e in events:
        if e.kind == "speech_burst":
            if e.t - last_burst < 3.0:
                continue
            last_burst = e.t
        out.append(e)
    return out


# ---------------------------------------------------------------------------
# moments + boundaries
# ---------------------------------------------------------------------------

ACOUSTIC = {"loudness_spike", "scream", "silence_break", "energy_burst", "bright_burst"}


def cluster(events: list[Ev]) -> list[list[Ev]]:
    groups: list[list[Ev]] = []
    for e in sorted(events, key=lambda e: e.t):
        if groups and e.t - max(x.t + x.duration for x in groups[-1]) <= CLUSTER_GAP_S:
            groups[-1].append(e)
        else:
            groups.append([e])
    return groups


def _peak_of(group: list[Ev]) -> float:
    acoustic = [e for e in group if e.kind in ("loudness_spike", "scream")]
    if acoustic:
        return max(acoustic, key=lambda e: e.strength).t
    return max(group, key=lambda e: e.strength).t


def _snap_start(words: list[Word], desired: float, lo: float) -> tuple[float, bool]:
    """Move the start to a pause/sentence boundary near ``desired`` (never mid-word)."""
    cands = []
    for i, w in enumerate(words):
        prev = words[i - 1] if i > 0 else None
        gap = w.start - prev.end if prev else 99
        sentence = prev is not None and prev.word.rstrip()[-1:] in ".!?"
        if gap >= 0.5 or sentence:
            cands.append(w.start - min(0.25, gap / 2 if gap < 99 else 0.25))
    window = [c for c in cands if desired - 6.0 <= c <= desired + 3.0 and c >= lo]
    if window:
        best = min(window, key=lambda c: (abs(c - desired) + (0 if c <= desired else 1.5)))
        return max(lo, best), True
    # inside a word? move to its start
    for w in words:
        if w.start < desired < w.end:
            return max(lo, w.start - 0.1), False
    return max(lo, desired), False


def _snap_end(words: list[Word], desired: float, hi: float) -> tuple[float, bool]:
    cands = []
    for i, w in enumerate(words):
        nxt = words[i + 1] if i + 1 < len(words) else None
        gap = nxt.start - w.end if nxt else 99
        if gap >= 0.5 or w.word.rstrip()[-1:] in ".!?":
            cands.append(w.end + min(0.35, gap / 2 if gap < 99 else 0.35))
    window = [c for c in cands if desired - 1.0 <= c <= desired + 8.0 and c <= hi]
    if window:
        best = min(window, key=lambda c: abs(c - desired) + (0 if c >= desired else 1.0))
        return min(hi, best), True
    for w in words:
        if w.start < desired < w.end:
            return min(hi, w.end + 0.15), False
    return min(hi, desired), False


def _settle_time(grid: Grid, base: np.ndarray, spread: np.ndarray, peak: float, max_extra: float) -> float:
    """First time after ``peak`` where loudness returned close to baseline for 1 s."""
    i0 = grid.i(peak)
    need = int(1.0 / HOP)
    calm = 0
    for i in range(max(0, i0), min(grid.n, i0 + int(max_extra / HOP))):
        v = grid.loud[i]
        if np.isnan(v) or (v - base[i]) / spread[i] < 1.0:
            calm += 1
            if calm >= need:
                return grid.t(i - need + 1)
        else:
            calm = 0
    return peak + max_extra


def _jaccard(a: str, b: str) -> float:
    sa = set(lexicon.normalize(a).split())
    sb = set(lexicon.normalize(b).split())
    if not sa or not sb:
        return 0.0
    return len(sa & sb) / len(sa | sb)


def _overlap(a0: float, a1: float, b0: float, b1: float) -> float:
    inter = max(0.0, min(a1, b1) - max(a0, b0))
    shortest = max(1e-6, min(a1 - a0, b1 - b0))
    return inter / shortest


def _clamp(v: float, lo: float = 0.0, hi: float = 10.0) -> float:
    return max(lo, min(hi, v))


# ---------------------------------------------------------------------------
# main entry point
# ---------------------------------------------------------------------------


def score_timeline(tl: dict) -> dict:
    settings = AISettings.model_validate(tl.get("settings") or {})
    t_from = float(tl["from"])
    t_to = float(tl["to"])
    scored_until = float(tl.get("scored_until", t_from))
    final = bool(tl.get("final"))
    data_start = float(tl.get("data_start", t_from))

    limit = t_to if final else t_to - settings.post_context_seconds
    if limit <= scored_until:
        return {"events": [], "candidates": [], "rejected": [], "scored_until": scored_until}

    words = [Word(float(w[0]), float(w[1]), str(w[2]), float(w[3]) if len(w) > 3 else 1.0)
             for w in tl.get("words", [])]
    words.sort(key=lambda w: w.start)
    grid = build_grid(tl.get("features", []), t_from, t_to)
    region = (scored_until, limit)
    events = detect_events(grid, words, region, settings.sensitivity)
    base, spread = rolling_baseline(grid.loud, int(BASE_WINDOW_S / HOP), int(BASE_GUARD_S / HOP)) if grid.n else (
        np.array([]), np.array([]))

    existing = list(tl.get("clips", []))
    candidates: list[Candidate] = []
    rejected: list[dict] = []  # every moment that did NOT become a clip, with the reason (diagnostics)

    def reject(group, reason: str, score: float | None = None, cand: Candidate | None = None) -> None:
        r = {"peak": round(_peak_of(group), 2), "reason": reason,
             "score": None if score is None else round(score, 1),
             "kinds": sorted({e.kind for e in group})}
        if cand is not None:  # full detail, persisted as a Candidate row (stats keep only the summary)
            r["detail"] = {"start": cand.start, "end": cand.end, "score": cand.score, "breakdown": cand.breakdown,
                           "features": cand.features, "transcript": cand.transcript[:1500]}
        rejected.append(r)

    for group in cluster(events):
        kinds = {e.kind for e in group}
        cand = _build_candidate(group, grid, base, spread, words, settings, data_start, t_to, existing)
        if not (kinds & ACOUSTIC) and sum(e.strength for e in group if e.kind in ("keyword", "laughter")) < 4.0:
            reject(group, "text_only", cand=cand)  # ordinary conversation: words without acoustic reaction
            continue
        if cand is None:
            reject(group, "too_short")
            continue
        if cand.score < settings.min_score:
            reject(group, "below_min_score", cand.score, cand)
            continue
        if any(_overlap(cand.start, cand.end, c["start"], c["end"]) >= settings.dedup_overlap for c in existing):
            reject(group, "duplicate", cand.score, cand)
            continue
        if any(_jaccard(cand.transcript, c.get("transcript", "")) >= 0.6 for c in existing if c.get("transcript")):
            reject(group, "duplicate", cand.score, cand)
            continue
        recent = [c for c in existing if cand.peak - c.get("peak", c["start"]) < 3600]
        if len(recent) >= settings.max_clips_per_hour and cand.score < min(100, settings.min_score + 20):
            reject(group, "rate_limit", cand.score, cand)
            continue
        meta = suggest_metadata(cand.transcript, cand.kinds, tl.get("streamer") or {}, settings.language)
        cand.title, cand.title_suggestions = meta["title"], meta["suggestions"]
        cand.description, cand.hashtags = meta["description"], meta["hashtags"]
        candidates.append(cand)
        existing.append({"start": cand.start, "end": cand.end, "peak": cand.peak, "transcript": cand.transcript,
                         "kinds": cand.kinds})

    return {
        "events": [asdict(e) for e in events],
        "candidates": [asdict(c) for c in candidates],
        "rejected": rejected,
        "min_score": settings.min_score,
        "scored_until": round(limit, 3),
        "scoring_version": SCORING_VERSION,
    }


def _build_candidate(group, grid, base, spread, words, settings: AISettings, data_start, t_to, existing):
    peak = _peak_of(group)
    first = min(e.t for e in group)
    last = max(e.t + e.duration for e in group)
    lo, hi = data_start, t_to

    desired_start = min(first, peak) - settings.pre_context_seconds
    settle = _settle_time(grid, base, spread, last, 10.0) if grid.n else last
    desired_end = max(last + settings.post_context_seconds * 0.6, peak + settings.post_context_seconds,
                      min(settle + 1.5, last + settings.post_context_seconds + 10.0))
    start, start_snapped = _snap_start(words, desired_start, lo)
    end, end_snapped = _snap_end(words, desired_end, hi)

    # duration constraints
    if end - start < settings.min_clip_seconds:
        extra = settings.min_clip_seconds - (end - start)
        start = max(lo, start - extra * 0.6)
        end = min(hi, end + (settings.min_clip_seconds - (end - start)))
    truncated = False
    if end - start > settings.max_clip_seconds:
        truncated = True
        start = max(start, peak - settings.max_clip_seconds * 0.6)
        end = start + settings.max_clip_seconds
    if end - start < min(settings.min_clip_seconds, 6.0):
        return None

    clip_words = [w for w in words if start <= w.start < end]
    transcript = " ".join(w.word for w in clip_words).strip()
    pre_words = [w for w in clip_words if w.start < peak]
    post_words = [w for w in clip_words if w.start >= peak]

    spikes = [e for e in group if e.kind == "loudness_spike"]
    delta = max((e.data.get("delta_db", 0) for e in spikes), default=0.0)
    zmax = max((e.strength for e in spikes), default=0.0)
    rise = max((e.data.get("rise_db", 0) for e in spikes), default=0.0)
    has = {k: [e for e in group if e.kind == k] for k in
           ("scream", "silence_break", "laughter", "keyword", "speech_burst", "energy_burst", "bright_burst")}
    kw_weight = sum(e.strength for e in has["keyword"])
    cats = [e.data.get("category") for e in has["keyword"]]

    reasons: list[str] = []
    intensity = _clamp(delta / 2.0 + zmax * 0.4)
    if delta >= 6:
        reasons.append(f"Pico de volume {delta:.0f} dB acima da média recente")
    if has["scream"]:
        intensity = _clamp(intensity + 2.5)
        reasons.append("Grito/reação intensa detectada (voz alta e aguda)")
    elif has["bright_burst"]:
        intensity = _clamp(intensity + 1.5)
        reasons.append("Som estridente/agudo acima do normal")
    surprise = _clamp(rise / 3.0 + (5.0 if has["silence_break"] else 0.0) + 1.0 * cats.count("surprise"))
    if has["silence_break"]:
        reasons.append("Silêncio seguido de acontecimento súbito")
    humor = _clamp(4.0 * len(has["laughter"]) + 1.5 * cats.count("humor") + (1.0 if has["energy_burst"] else 0))
    if has["laughter"]:
        reasons.append("Risadas identificadas na fala")
    reaction = _clamp(kw_weight * 1.4 + (3.0 if has["scream"] else 0) + 2.0 * len(has["speech_burst"]))
    phrases = list(dict.fromkeys(e.data.get("phrase") for e in sorted(has["keyword"], key=lambda e: -e.strength)))[:3]
    if phrases:
        reasons.append("Reação verbal: " + ", ".join(f"“{p}”" for p in phrases))
    if has["speech_burst"]:
        reasons.append("Fala acelerada (reação do streamer)")
    narrative = _clamp((3.0 if len(pre_words) >= 6 else len(pre_words) * 0.5)
                       + (3.0 if len(post_words) >= 3 else len(post_words))
                       + (2.0 if start_snapped else 0) + (2.0 if end_snapped else 0))
    if truncated:
        narrative = _clamp(narrative - 3)
    pre_speech = sum(w.end - w.start for w in pre_words)
    context = _clamp(10.0 * pre_speech / max(1.0, peak - start) * 1.6)
    sim = max((_jaccard(transcript, c.get("transcript", "")) for c in existing), default=0.0)
    recent_kinds = [k for c in existing[-3:] for k in c.get("kinds", [])]
    dominant = max(({e.kind for e in group} - {"speech_burst"}) or {"loudness_spike"},
                   key=lambda k: sum(1 for e in group if e.kind == k))
    uniqueness = _clamp(10.0 * (1.0 - sim) - (2.0 if recent_kinds.count(dominant) >= 2 else 0.0))
    audience = _clamp(float(np.mean(sorted([intensity, surprise, humor, reaction])[-2:])))

    breakdown = {
        "intensity": round(intensity, 2),
        "surprise": round(surprise, 2),
        "humor": round(humor, 2),
        "reaction": round(reaction, 2),
        "narrative": round(narrative, 2),
        "context": round(context, 2),
        "uniqueness": round(uniqueness, 2),
        "audience": round(audience, 2),
        "visual": None,  # filled by the vision worker when available
        "chat": None,  # chat analysis not available in this build
    }
    # Phase 2 rule (only when acoustic classes exist): loudness is not emotion. A spike counts
    # as a streamer reaction only when corroborated by reaction words, laughter or screams.
    sources = {e.data.get("source") for e in spikes}
    corroborated = bool(has["keyword"] or has["laughter"] or any(e.data.get("by") == "yamnet" for e in has["scream"]))
    uncorroborated = bool(spikes) and grid.has_cats and not corroborated
    game_only = uncorroborated and sources <= {"game"}
    if uncorroborated:
        breakdown["intensity"] = round(intensity * 0.4, 2)
        breakdown["reaction"] = round(reaction * 0.5, 2)
    total = weighted_total(breakdown, settings)
    raw_total = total
    if game_only:
        total *= 0.6
        reasons.append("Penalidade: o pico de volume vem do jogo (explosão/tiro), sem reação do streamer")
    elif uncorroborated:
        total *= 0.6
        reasons.append("Penalidade: pico de volume sem reação confirmada do streamer (pode ser som do jogo)")
    if intensity < 3 and reaction < 3 and humor < 3:
        total *= 0.6
        reasons.append("Penalidade: pouca reação/intensidade (provável conversa normal)")
    if truncated:
        reasons.append("Momento longo: limitado à duração máxima")
    if not reasons:
        reasons.append("Variação acústica acima do normal")
    pi = grid.i(peak) if grid.n else 0
    yam = {f"yam_{c}": (round(v, 3) if math.isfinite(v := grid.cat(c, pi - 12, pi + 12)) else None)
           for c in ACOUSTIC_CATS} if grid.n and grid.has_cats else {f"yam_{c}": None for c in ACOUSTIC_CATS}
    dur = max(1e-6, end - start)
    features = {
        **{f"dim_{k}": v for k, v in breakdown.items() if v is not None},
        "delta_db": round(float(delta), 2), "z_max": round(float(zmax), 2), "rise_db": round(float(rise), 2),
        "kw_weight": round(float(kw_weight), 2),
        **{f"n_{k}": len(v) for k, v in has.items()}, "n_loudness_spike": len(spikes), "n_events": len(group),
        "scream_by_yamnet": int(any(e.data.get("by") == "yamnet" for e in has["scream"])),
        "cat_surprise": cats.count("surprise"), "cat_humor": cats.count("humor"),
        "cat_tension": cats.count("tension"), "cat_other": sum(1 for c in cats if c not in ("surprise", "humor", "tension")),
        "group_span_s": round(last - first, 2), "duration_s": round(dur, 2),
        "pre_words": len(pre_words), "post_words": len(post_words), "words_per_s": round(len(clip_words) / dur, 3),
        "pre_speech_ratio": round(pre_speech / max(1.0, peak - start), 3),
        "start_snapped": int(start_snapped), "end_snapped": int(end_snapped), "truncated": int(truncated),
        "uncorroborated": int(uncorroborated), "game_only": int(game_only),
        "low_reaction": int(intensity < 3 and reaction < 3 and humor < 3),
        "similarity_prev": round(float(sim), 3), "dominant_repeat": int(recent_kinds.count(dominant) >= 2),
        "raw_total": round(float(raw_total), 2), "score": round(float(total), 2),
        **yam,
    }
    return Candidate(
        start=round(start, 2), end=round(end, 2), peak=round(peak, 2), score=round(total, 1),
        breakdown=breakdown, reasons=reasons, transcript=transcript,
        kinds=sorted({e.kind for e in group}), features=features,
    )


def weighted_total(breakdown: dict, settings: AISettings) -> float:
    w = settings.weights.model_dump()
    num = 0.0
    den = 0.0
    for k, v in breakdown.items():
        if v is None or k not in w:
            continue
        num += w[k] * float(v)
        den += w[k]
    return 0.0 if den == 0 else 10.0 * num / den
