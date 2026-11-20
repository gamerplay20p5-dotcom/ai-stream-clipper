"""Visual format from a screenshot (Phase 6C): what the layout IS, as abstract parameters.

Two layers, combined field by field (each field records its source and confidence):
* measured (OpenCV, deterministic): aspect, split line between webcam and gameplay, face position,
  caption band (position/size/colours), palette and contrast, a small circle in a corner (avatar badge);
* described by the existing vision-language model (Qwen3-VL), restricted to closed categories.

A measurement beats the model; the model fills what cannot be measured; nothing is invented (unknown
stays at the default and says so). Nothing of the image is copied into the profile: no crops, no
logos, no text - only positions, sizes, colours and styles, which our renderer applies to OUR video.
"""

from __future__ import annotations


import numpy as np

VERSION = "format-2026-11-19a"
ENUMS = {
    "webcam_position": ("top", "bottom", "corner", "none"),
    "caption_position": ("high", "middle", "low", "none"),
    "caption_style": ("bold", "boxed", "clean", "none"),
    "caption_case": ("upper", "mixed"),
    "badge_position": ("top_left", "top_right", "bottom_left", "bottom_right", "none"),
}
PROMPT = """Você vê um quadro de um vídeo vertical curto (Shorts/Reels/TikTok). Descreva SÓ a organização visual,
sem ler nem copiar textos, nomes ou logotipos. Responda apenas com JSON, usando estes valores:
{"webcam_position": "top|bottom|corner|none", "caption_position": "high|middle|low|none",
"caption_style": "bold|boxed|clean|none", "caption_case": "upper|mixed",
"badge_position": "top_left|top_right|bottom_left|bottom_right|none"}
bold = letras grossas com contorno; boxed = texto sobre caixa/fundo; clean = texto simples.
badge = foto de perfil pequena com nome. Se não houver, use none. /no_think"""
DEFAULTS = {"layout": "gameplay", "webcam": {"position": "top", "height_pct": 37},
            "captions": {"position": "low", "size_pct": 100, "style": "bold", "uppercase": True,
                         "active_color": "", "emphasis_color": ""},
            "badge": {"enabled": False, "position": "top_left", "shape": "circle", "name": ""}}


def _hex(bgr) -> str:
    b, g, r = (int(max(0, min(255, x))) for x in bgr)
    return f"#{r:02X}{g:02X}{b:02X}"


def palette(img: np.ndarray, k: int = 5) -> list[dict]:
    import cv2

    small = cv2.resize(img, (64, max(1, int(64 * img.shape[0] / img.shape[1]))), interpolation=cv2.INTER_AREA)
    px = small.reshape(-1, 3).astype(np.float32)
    k = min(k, len(px))
    _c, labels, centers = cv2.kmeans(px, k, None, (cv2.TERM_CRITERIA_EPS + cv2.TERM_CRITERIA_MAX_ITER, 20, 1.0), 3,
                                     cv2.KMEANS_PP_CENTERS)
    counts = np.bincount(labels.ravel(), minlength=k)
    order = np.argsort(-counts)
    return [{"color": _hex(centers[i]), "share": round(float(counts[i] / counts.sum()), 3)} for i in order]


def split_line(gray: np.ndarray, img: np.ndarray | None = None) -> tuple[float | None, float]:
    """Row (as a fraction of the height) of a full-width horizontal boundary in the 20-80% band,
    and its strength. A webcam/gameplay split is a long straight edge with different content above."""
    h, w = gray.shape
    g = gray.astype(np.float32)
    if img is not None:  # colour edges: two areas can differ in hue with almost the same brightness
        diff = np.linalg.norm(np.diff(img.astype(np.float32), axis=0), axis=2)
    else:
        diff = np.abs(np.diff(g, axis=0))  # (h-1, w)
    full = (diff > 25).mean(axis=1)  # share of the width crossing a strong edge on each row
    lo, hi = int(h * 0.2), int(h * 0.8)
    if hi <= lo:
        return None, 0.0
    y = lo + int(np.argmax(full[lo:hi]))
    strength = float(full[y])
    src = img.astype(np.float32) if img is not None else g[..., None]
    pad = max(3, h // 200)  # skip a thin separator line drawn on the boundary itself
    above = src[max(0, y - h // 10):max(1, y - pad)].reshape(-1, src.shape[-1]).mean(axis=0)
    below = src[y + 1 + pad:y + 1 + pad + h // 10].reshape(-1, src.shape[-1]).mean(axis=0)
    if strength >= 0.5 and float(np.linalg.norm(above - below)) > 12:  # different content (colour) on each side
        return round(y / h, 3), round(strength, 2)
    return None, round(strength, 2)


def caption_band(img: np.ndarray) -> dict | None:
    """Rows with bright text outlined by dark pixels (the usual burned caption), outside the edges."""
    import cv2

    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    h, w = gray.shape
    bright = gray > 200
    dark = cv2.dilate((gray < 50).astype(np.uint8), np.ones((5, 5), np.uint8)).astype(bool)
    text = bright & dark
    rows = text[:, int(w * 0.08):int(w * 0.92)].mean(axis=1)
    if rows.max() < 0.02:
        return None
    thr = max(0.02, rows.max() * 0.35)
    ys = np.where(rows >= thr)[0]
    groups, cur = [], [ys[0]]
    for y in ys[1:]:
        if y - cur[-1] > max(4, h // 100):
            groups.append(cur)
            cur = [y]
        else:
            cur.append(y)
    groups.append(cur)
    g = max(groups, key=lambda grp: rows[grp].sum())
    y0, y1 = int(g[0]), int(g[-1])
    if y1 - y0 < h * 0.008:  # thinner than ~15 px at 1920: not a caption line
        return None
    band = img[y0:y1 + 1]
    hsv = cv2.cvtColor(band, cv2.COLOR_BGR2HSV)
    vivid = (hsv[:, :, 1] > 120) & (hsv[:, :, 2] > 170)  # highlighted word (e.g. yellow/green)
    accent = _hex(np.median(band[vivid], axis=0)) if vivid.sum() > 30 else ""
    return {"y0": round(y0 / h, 3), "y1": round(y1 / h, 3), "center": round((y0 + y1) / 2 / h, 3),
            "height": round((y1 - y0) / h, 3), "accent": accent,
            "boxed": bool((gray[y0:y1 + 1] < 60).mean() > 0.55)}


def faces(img: np.ndarray) -> list[dict]:
    import cv2
    from pathlib import Path

    try:
        clf = cv2.CascadeClassifier(str(Path(cv2.data.haarcascades) / "haarcascade_frontalface_default.xml"))
    except Exception:
        return []
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    h, w = gray.shape
    out = []
    for x, y, fw, fh in clf.detectMultiScale(gray, 1.15, 5, minSize=(max(24, w // 20), max(24, w // 20))):
        out.append({"cx": round((x + fw / 2) / w, 3), "cy": round((y + fh / 2) / h, 3), "size": round(fw / w, 3)})
    return out


def corner_circle(img: np.ndarray) -> str | None:
    """A small circle (profile picture) in one corner -> its position, else None."""
    import cv2

    gray = cv2.medianBlur(cv2.cvtColor(img, cv2.COLOR_BGR2GRAY), 5)
    h, w = gray.shape
    rmin, rmax = int(w * 0.03), int(w * 0.09)
    circles = cv2.HoughCircles(gray, cv2.HOUGH_GRADIENT, 1.2, w // 8, param1=120, param2=28, minRadius=rmin,
                               maxRadius=rmax)
    if circles is None:
        return None
    for x, y, r in circles[0]:
        horiz = "left" if x < w * 0.3 else "right" if x > w * 0.7 else None
        vert = "top" if y < h * 0.22 else "bottom" if y > h * 0.78 else None
        if horiz and vert:
            return f"{vert}_{horiz}"
    return None


def measure(img: np.ndarray) -> dict:
    import cv2

    h, w = img.shape[:2]
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    split, strength = split_line(gray, img)
    fs = faces(img)
    return {"width": w, "height": h, "aspect": round(w / h, 3), "vertical": h > w * 1.3,
            "split": split, "split_strength": strength, "faces": fs, "caption": caption_band(img),
            "palette": palette(img), "contrast": round(float(gray.std() / 128), 2), "badge": corner_circle(img)}


def parse_vlm(raw: str) -> dict:
    """Closed categories only: anything else is dropped (never a free-text value in the profile)."""
    from .jsonparse import extract_json

    obj = extract_json(raw or "") or {}
    # closed categories ONLY: free text is never kept (measured on real frames, 2026-11-19: the
    # model transcribed a third party's username into a "notes" field despite the instruction)
    return {k: obj.get(k) for k in ENUMS if obj.get(k) in ENUMS[k]}


def build_params(m: dict, v: dict | None = None) -> dict:
    """Merge measurement + model into the renderer's parameters, with source and confidence per field."""
    v = v or {}
    p = {"version": VERSION, **{k: dict(x) if isinstance(x, dict) else x for k, x in DEFAULTS.items()}}
    src: dict[str, str] = {}
    conf: dict[str, float] = {}
    suggestions: list[str] = []

    def put(field, value, source, c):
        node, key = field.split(".") if "." in field else (None, field)
        (p[node] if node else p)[key] = value
        src[field], conf[field] = source, round(c, 2)

    # webcam / layout: measured split first, faces confirm which side
    face_top = any(f["cy"] < (m.get("split") or 0.5) for f in m.get("faces") or [])
    face_bottom = any(f["cy"] > (m.get("split") or 0.5) for f in m.get("faces") or [])
    if m.get("split") is not None and (face_top or face_bottom or v.get("webcam_position") in ("top", "bottom")):
        pos = "top" if (face_top and not face_bottom) else "bottom" if face_bottom and not face_top else \
            (v.get("webcam_position") if v.get("webcam_position") in ("top", "bottom") else "top")
        share = m["split"] if pos == "top" else 1 - m["split"]
        put("layout", "split", "medido", 0.85)
        put("webcam.position", pos, "medido" if (face_top or face_bottom) else "IA visual", 0.8)
        put("webcam.height_pct", int(round(max(20, min(60, share * 100)))), "medido", 0.85)
    elif v.get("webcam_position") in ("top", "bottom", "corner"):
        suggestions.append(f"a IA visual sugere webcam '{v['webcam_position']}', mas a divisão não foi medida: "
                           f"layout mantido (confirme manualmente)")
    elif v.get("webcam_position") == "none" or (m.get("split") is None and not m.get("faces")):
        put("layout", "gameplay", "medido" if m.get("split") is None else "IA visual", 0.6)
    # captions
    cap = m.get("caption")
    if cap:
        pos = "high" if cap["center"] < 0.4 else "middle" if cap["center"] < 0.66 else "low"
        put("captions.position", pos, "medido", 0.8)
        font_px = cap["height"] * 1920 * 0.8
        put("captions.size_pct", int(round(max(60, min(160, font_px / 84 * 100)))), "medido", 0.6)
        put("captions.style", "boxed" if cap["boxed"] else (v.get("caption_style") if v.get("caption_style") in
                                                            ("bold", "clean") else "bold"),
            "medido" if cap["boxed"] else ("IA visual" if v.get("caption_style") else "padrão"), 0.6)
        if cap.get("accent"):
            put("captions.active_color", cap["accent"], "medido", 0.6)
    elif v.get("caption_position") in ("high", "middle", "low"):
        put("captions.position", v["caption_position"], "IA visual", 0.45)
        if v.get("caption_style") in ("bold", "boxed", "clean"):
            put("captions.style", v["caption_style"], "IA visual", 0.45)
    elif v.get("caption_position") == "none":
        put("captions.style", "none", "IA visual", 0.45)
    if v.get("caption_case") in ("upper", "mixed"):
        put("captions.uppercase", v["caption_case"] == "upper", "IA visual", 0.5)
    # badge (OUR avatar + OUR name are supplied by the owner; only the position is taken)
    if m.get("badge"):
        put("badge.enabled", True, "medido", 0.6)
        put("badge.position", m["badge"], "medido", 0.6)
    elif v.get("badge_position") and v["badge_position"] != "none":
        suggestions.append(f"a IA visual sugere um selo em '{v['badge_position']}', não confirmado pela medição")
    p["palette"] = [x["color"] for x in (m.get("palette") or [])][:5]
    p["contrast"] = m.get("contrast")
    p["sources"], p["confidence"] = src, conf
    p["notes"] = suggestions  # fixed-vocabulary, unconfirmed suggestions (never text read from the image)
    return p


def analyze(img: np.ndarray, vlm=None) -> dict:
    """``vlm``: callable(prompt, img) -> raw text, or None (measurement only)."""
    m = measure(img)
    v, vlm_error = {}, ""
    if vlm is not None:
        try:
            v = parse_vlm(vlm(PROMPT, img))
        except Exception as exc:  # the model is optional: a failure never loses the measurement
            vlm_error = f"{type(exc).__name__}: {str(exc)[:160]}"
    return {"measured": m, "vlm": v, "vlm_error": vlm_error, "params": build_params(m, v), "version": VERSION}
