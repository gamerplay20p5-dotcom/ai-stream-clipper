"""Acoustic event classification (YAMNet, ONNX on CPU) and voice prosody.

Purpose: distinguish *what* made a sound - the streamer laughing or screaming versus an
explosion, gunfire or music in the game - so that loudness alone is never taken as
emotion. YAMNet scores are grouped into a few clip-relevant categories.
"""

from __future__ import annotations

import csv
import logging
import threading
from pathlib import Path

import numpy as np

log = logging.getLogger(__name__)

HOP_S = 0.48  # YAMNet frame hop
CATEGORIES = ("speech", "laughter", "scream", "shout", "game_impact", "gunfire", "vehicle", "music", "crowd")

_KEYWORDS: dict[str, tuple[str, ...]] = {
    "laughter": ("laughter", "laugh", "giggle", "snicker", "chuckle", "chortle"),
    "scream": ("screaming", "whimper", "wail, moan"),
    "shout": ("shout", "yell", "battle cry", "children shouting"),
    "speech": ("speech", "conversation", "narration", "speech synthesizer"),
    "game_impact": ("explosion", "boom", "burst, pop", "smash, crash", "breaking", "bang", "slam", "thump, thud",
                    "fireworks", "eruption", "crushing", "shatter"),
    "gunfire": ("gunshot", "machine gun", "fusillade", "artillery fire", "cap gun"),
    "vehicle": ("vehicle", "car", "engine", "skidding", "tire squeal", "siren", "accelerating", "traffic",
                "motorcycle", "truck", "race car"),
    "music": ("music", "musical", "song", "singing"),
    "crowd": ("cheering", "crowd", "applause", "hubbub"),
}
VOICE = ("speech", "laughter", "scream", "shout")


def _category_of(name: str) -> str | None:
    low = name.lower()
    # order matters: laughter/scream/shout before generic speech, gunfire before impacts
    for cat in ("laughter", "scream", "shout", "gunfire", "game_impact", "crowd", "speech", "vehicle", "music"):
        if any(k in low for k in _KEYWORDS[cat]):
            if cat == "speech" and "music" in low:
                continue
            return cat
    return None


class YamnetClassifier:
    def __init__(self, model_dir: Path, threads: int = 2):
        import onnxruntime as ort

        so = ort.SessionOptions()
        so.intra_op_num_threads = threads
        so.inter_op_num_threads = 1
        self.session = ort.InferenceSession(str(model_dir / "yamnet.onnx"), so, providers=["CPUExecutionProvider"])
        self.input_name = self.session.get_inputs()[0].name
        names = []
        with open(model_dir / "yamnet_class_map.csv", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                names.append(row["display_name"])
        self.class_names = names
        self.cat_index: dict[str, list[int]] = {c: [] for c in CATEGORIES}
        for i, n in enumerate(names):
            c = _category_of(n)
            if c:
                self.cat_index[c].append(i)
        self._lock = threading.Lock()

    def classify(self, samples: np.ndarray) -> dict:
        """``samples``: mono float32 at 16 kHz. Returns ``{"hop", "cats": {cat: [score...]},
        "top": [[label, score], ...]}`` with one entry per 0.48 s frame."""
        if len(samples) < 16000:
            samples = np.pad(samples, (0, 16000 - len(samples)))
        with self._lock:
            scores = self.session.run(None, {self.input_name: samples.astype(np.float32)})[0]
        cats = {c: [round(float(v), 3) for v in (scores[:, idx].max(axis=1) if idx else np.zeros(len(scores)))]
                for c, idx in self.cat_index.items()}
        top_i = scores.argmax(axis=1)
        top = [[self.class_names[i], round(float(scores[k, i]), 3)] for k, i in enumerate(top_i)]
        return {"hop": HOP_S, "cats": cats, "top": top}


_cache: dict[str, YamnetClassifier] = {}


def get_classifier(models_dir: Path) -> YamnetClassifier | None:
    d = Path(models_dir) / "yamnet"
    if not (d / "yamnet.onnx").exists():
        return None
    key = str(d)
    if key not in _cache:
        try:
            _cache[key] = YamnetClassifier(d)
        except Exception as exc:  # missing onnxruntime, corrupt model...
            log.warning("yamnet unavailable", extra={"err": str(exc)[:200]})
            return None
    return _cache[key]


# ---------------------------------------------------------------------------
# prosody (pitch) - NumPy autocorrelation, voiced frames only
# ---------------------------------------------------------------------------


def pitch_track(samples: np.ndarray, sr: int = 16000, hop: float = 0.25, fmin: float = 75.0,
                fmax: float = 600.0) -> list[float]:
    """Median F0 (Hz) per ``hop`` bin, 0 when unvoiced. Raised pitch together with voice
    activity is a stronger emotion cue than loudness alone."""
    frame = int(0.04 * sr)
    step = int(0.02 * sr)
    n_bins = max(1, int(np.ceil(len(samples) / (hop * sr))))
    out = [0.0] * n_bins
    if len(samples) < frame:
        return out
    lag_min, lag_max = int(sr / fmax), int(sr / fmin)
    win = np.hanning(frame).astype(np.float32)
    per_bin: dict[int, list[float]] = {}
    for start in range(0, len(samples) - frame, step):
        x = samples[start:start + frame] * win
        energy = float(np.sqrt(np.mean(x**2)))
        if energy < 0.01:
            continue
        x = x - x.mean()
        ac = np.correlate(x, x, mode="full")[frame - 1:]
        if ac[0] <= 0:
            continue
        ac = ac / ac[0]
        seg = ac[lag_min:lag_max]
        if len(seg) == 0:
            continue
        k = int(np.argmax(seg))
        if seg[k] < 0.45:  # weak periodicity -> noise, not voice
            continue
        f0 = sr / (lag_min + k)
        b = min(n_bins - 1, int((start + frame / 2) / (hop * sr)))
        per_bin.setdefault(b, []).append(f0)
    for b, v in per_bin.items():
        out[b] = round(float(np.median(v)), 1)
    return out
