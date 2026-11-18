"""Edit plan builder: turns analysis results into a structured :class:`EditPlan` that the
FFmpeg renderer executes. No generative video - only reframing, zooms, captions,
transitions and loudness normalisation of the original footage.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from ..analysis import lexicon
from .schemas import CropKeyframe, DirectorDecision, EditPlan, VisualAnalysis, ZoomMoment


@dataclass(frozen=True)
class Template:
    key: str
    label: str
    caption_style: str
    zoom_scale: float  # 1.0 disables punch-ins
    fade_in: float
    fade_out: float
    show_title: bool


TEMPLATES: dict[str, Template] = {
    "padrao": Template("padrao", "Padrão", "bold", 1.10, 0.2, 0.35, True),
    "dinamico": Template("dinamico", "Dinâmico", "bold", 1.18, 0.12, 0.25, True),
    "minimalista": Template("minimalista", "Minimalista", "clean", 1.0, 0.3, 0.5, False),
}


def smooth_keyframes(points: list[tuple[float, float]], max_kf: int = 12, max_speed: float = 0.25) -> list[CropKeyframe]:
    """Moving-average the motion centroid, cap pan speed (fraction of width per second) and
    decimate to at most ``max_kf`` keyframes."""
    if not points:
        return []
    pts = sorted(points)
    t = np.array([p[0] for p in pts])
    x = np.array([p[1] for p in pts])
    if len(x) >= 3:
        k = np.ones(3) / 3
        x = np.convolve(np.pad(x, 1, mode="edge"), k, mode="valid")
    for i in range(1, len(x)):  # limit pan speed
        dt = max(0.05, t[i] - t[i - 1])
        x[i] = float(np.clip(x[i], x[i - 1] - max_speed * dt, x[i - 1] + max_speed * dt))
    idx = np.unique(np.linspace(0, len(t) - 1, min(max_kf, len(t))).round().astype(int))
    return [CropKeyframe(t=round(float(t[i]), 2), cx=round(float(np.clip(x[i], 0.2, 0.8)), 3)) for i in idx]


def build(decision: DirectorDecision, *, template_key: str = "padrao", title: str = "", transcript_words: list[str] | None = None,
          visual: VisualAnalysis | None = None, motion_cx: list[tuple[float, float]] | None = None,
          webcam_region: dict | None = None, face_track: list[dict] | None = None, layout_override: str | None = None,
          caption_override: str | None = None, normalize_audio: bool = True, zoom_intensity: float = 1.0) -> EditPlan:
    """``motion_cx``: (t_abs, normalised x of the motion centroid); ``face_track`` entries use
    session offsets in ``t``. Output times are clip-relative."""
    tpl = TEMPLATES.get(template_key, TEMPLATES["padrao"])
    s0 = decision.start_abs
    dur = decision.end_abs - decision.start_abs
    rationale: list[str] = []

    # layout
    action = visual.action if visual is not None and not visual.fallback else 0.0
    kf: list[CropKeyframe] = []
    if layout_override and layout_override != "auto":
        layout = layout_override
        rationale.append(f"Enquadramento escolhido manualmente: {layout}")
    elif webcam_region:
        layout = "split"
        rationale.append("Webcam localizada: rosto em cima, gameplay embaixo")
    else:
        pts = [(t - s0, cx) for t, cx in (motion_cx or []) if 0 <= t - s0 <= dur]
        spread = float(np.std([p[1] for p in pts])) if len(pts) >= 4 else 1.0
        if pts and (action >= 6 or spread < 0.12) and len(pts) >= 4:
            layout = "action"
            kf = smooth_keyframes(pts)
            rationale.append("Sem webcam e ação concentrada: recorte vertical acompanha o movimento")
        else:
            layout = "gameplay"
            rationale.append("Sem webcam e ação espalhada: tela inteira para não cortar informação")
    if layout == "action" and not kf:
        kf = smooth_keyframes([(t - s0, cx) for t, cx in (motion_cx or []) if 0 <= t - s0 <= dur]) or \
            [CropKeyframe(t=0, cx=0.5)]

    # moderate punch-in at the peak
    zooms: list[ZoomMoment] = []
    scale = 1.0 + (tpl.zoom_scale - 1.0) * max(0.0, min(1.5, zoom_intensity))
    if scale > 1.01 and decision.score >= 60:
        t_peak = max(0.0, decision.peak_abs - s0 - 0.15)
        if t_peak + 1.4 < dur:
            zooms.append(ZoomMoment(t=round(t_peak, 2), duration=1.4, scale=round(min(1.35, scale), 3)))
            rationale.append(f"Zoom moderado ({scale:.2f}x) no pico do acontecimento")

    emphasis: list[str] = []
    toks = transcript_words or []
    for i0, i1, *_ in lexicon.find_hits(lexicon.tokenize(toks)):
        for w in toks[i0:i1 + 1]:
            clean = w.strip(".,!?;:\"'()").lower()
            if clean and clean not in emphasis:
                emphasis.append(clean)
    tracks = [{**f, "t": round(f["t"] - s0, 2)} for f in (face_track or []) if 0 <= f["t"] - s0 <= dur]
    return EditPlan(
        template=tpl.key, layout=layout, webcam_region=webcam_region, face_track=tracks[:60], crop_keyframes=kf,
        zooms=zooms, caption_style=caption_override or tpl.caption_style, emphasis_words=emphasis[:30],
        fade_in=tpl.fade_in, fade_out=tpl.fade_out, normalize_audio=normalize_audio,
        title=title if tpl.show_title else "", rationale=rationale,
        source="ai" if visual is not None and not visual.fallback else "heuristic",
    )
