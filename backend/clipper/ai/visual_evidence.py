"""Cross-check semantic and webcam observations against frame timestamps."""

from __future__ import annotations

from statistics import median

from ..analysis.lexicon import normalize
from .schemas import VisualAnalysis

_OBSERVATIONS = {
    "rollover": ("capot", "rollover", "roll over", "overturn"),
    "collision": ("colis", "batid", "bate", "collision", "crash"),
    "fall": ("cai", "queda", "fall", "fell"),
    "police_chase": ("perseguicao policial", "policia persegue", "police chase", "chased by police"),
    "escape": ("consegue fugir", "escapa", "escapou", "gets away", "escapes"),
    "failed_escape": ("fuga falha", "nao consegue fugir", "failed escape", "fails to escape"),
    "prank": ("troll", "pegadinha", "prank"),
    "funny_interaction": ("interacao engracada", "piada", "funny interaction", "joke"),
    "surprise": ("inesperad", "surprise", "unexpected"),
    "unlikely_win": ("vitoria improvavel", "vence contra", "unlikely win", "comeback win"),
    "unexpected_failure": ("falha inesperada", "unexpected failure", "unexpectedly fails"),
}


def corroborated_event(visual: VisualAnalysis | None, peak: float, start: float, end: float) -> bool:
    if visual is None or visual.fallback or visual.event_kind == "unknown" or visual.confidence < 0.75:
        return False
    w0 = visual.window_abs[0]
    shown = sorted(set(t for t in visual.frame_times if start <= w0 + t <= end))
    if len(shown) < 3:
        return False
    for event in visual.events:
        label = normalize(event.label)
        observed = f" {label} "
        if visual.event_kind != "failed_escape" and (
                any(marker in observed for marker in (" sem ", " nao ", " without ", " not ")) or
                label.startswith(("no crash", "no collision", "no rollover", "no fall"))):
            continue
        if any(f" {term}" in observed for term in _OBSERVATIONS[visual.event_kind]) and \
                event.importance >= 6 and abs(w0 + event.t - peak) <= 3 and (
                shown[0] + 0.25 <= event.t <= shown[-1] - 0.25
                and min(abs(t - event.t) for t in shown) <= 0.6):
            return True
    return False


def webcam_reaction(track: list[dict], peak: float) -> dict | None:
    """Detect sustained head or lower-face changes inside a configured webcam."""
    baseline = [f for f in track if peak - 6 <= f["t"] <= peak - 0.35]
    after = [f for f in track if peak <= f["t"] <= peak + 4]
    if len(baseline) < 3 or len(after) < 2:
        return None
    reference = {k: median(f[k] for f in baseline) for k in ("x", "y", "w", "h")}
    if min(reference["w"], reference["h"]) <= 0:
        return None

    def deviation(f: dict) -> float:
        dx = (f["x"] + f["w"] / 2 - reference["x"] - reference["w"] / 2) / reference["w"]
        dy = (f["y"] + f["h"] / 2 - reference["y"] - reference["h"] / 2) / reference["h"]
        size = abs(f["h"] / reference["h"] - 1)
        return max(abs(dx), abs(dy), size)

    noise = median(deviation(f) for f in baseline)
    if noise > 0.18 or peak - baseline[-1]["t"] > 1.8:
        return None
    threshold = max(0.22, 3 * noise)
    mouth_baseline = [f["mouth"] for f in baseline if "mouth" in f]
    mouth_ref = median(mouth_baseline) if len(mouth_baseline) >= 3 else None
    mouth_noise = median(abs(v - mouth_ref) for v in mouth_baseline) if mouth_ref is not None else 1.0
    for first, second in zip(after, after[1:]):
        if second["t"] - first["t"] > 1.5:
            continue
        signal = ""
        if min(deviation(first), deviation(second)) >= threshold:
            signal = "head"
        elif mouth_ref is not None and mouth_noise <= 0.1 and "mouth" in first and "mouth" in second and (
                min(first["mouth"], second["mouth"]) - mouth_ref >= max(0.16, 3 * mouth_noise)):
            signal = "lower_face"
        if signal:
            return {"start": first["t"], "end": second["t"], "signal": signal}
    return None
