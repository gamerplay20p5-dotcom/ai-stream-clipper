"""Visual analysis providers (layer 2).

* :class:`HeuristicVisionProvider` - OpenCV only, runs anywhere (CPU). Measures scene
  changes, motion and flashes around the peak and localises a webcam overlay by
  finding a face that stays in the same place across sampled frames.
* :class:`OpenVINOVLMProvider` - **experimental**: a small vision-language model through
  ``openvino_genai.VLMPipeline`` on an Intel Arc GPU. Requires a model exported to
  OpenVINO IR (e.g. with ``optimum-cli export openvino``). Not validated in CI - see
  docs/VISION.md for the validation procedure.

Both return :class:`VisionResult`; the API merges ``visual_score`` into the clip score.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Protocol

import numpy as np

log = logging.getLogger(__name__)


@dataclass
class VisionResult:
    provider: str
    visual_score: float  # 0..10
    scene_changes: list[float] = field(default_factory=list)  # clip-relative seconds
    motion_peak_t: float | None = None
    webcam_region: dict | None = None  # normalised x,y,w,h
    description: str = ""
    reasons: list[str] = field(default_factory=list)
    frames_analyzed: int = 0
    latency_s: float = 0.0
    extra: dict = field(default_factory=dict)

    def as_dict(self) -> dict:
        return asdict(self)


@dataclass
class Frame:
    t: float  # seconds relative to clip start
    image: np.ndarray  # BGR


class VisionProvider(Protocol):
    name: str
    requires_gpu: bool

    def analyze(self, frames: list[Frame], peak_t: float, transcript: str) -> VisionResult: ...


def sample_frames(path: Path, start: float, end: float, peak: float, interval: float, max_frames: int = 120) -> list[Frame]:
    """Adaptive sampling: dense (interval/3) within +-3 s of the peak, ``interval`` elsewhere.
    ``start``/``end``/``peak`` are relative to the beginning of ``path``."""
    import cv2

    times: list[float] = []
    t = max(0.0, start)
    while t < end and len(times) < max_frames:
        times.append(t)
        step = interval / 3.0 if abs(t - peak) <= 3.0 else interval
        t += max(0.1, step)
    cap = cv2.VideoCapture(str(path))
    frames: list[Frame] = []
    try:
        for ts in times:
            cap.set(cv2.CAP_PROP_POS_MSEC, ts * 1000.0)
            ok, img = cap.read()
            if not ok or img is None:
                continue
            h, w = img.shape[:2]
            if w > 960:
                img = cv2.resize(img, (960, int(h * 960 / w)), interpolation=cv2.INTER_AREA)
            frames.append(Frame(ts - start, img))
    finally:
        cap.release()
    return frames


class HeuristicVisionProvider:
    name = "heuristic-opencv"
    requires_gpu = False

    def __init__(self):
        import cv2

        self._cv2 = cv2
        self._face = None
        try:  # Haar cascades ship with OpenCV 4.x; without them webcam detection is disabled
            cascade = Path(cv2.data.haarcascades) / "haarcascade_frontalface_default.xml"
            self._face = cv2.CascadeClassifier(str(cascade))
        except AttributeError:
            log.warning("OpenCV build without CascadeClassifier: webcam detection disabled")

    def analyze(self, frames: list[Frame], peak_t: float, transcript: str) -> VisionResult:
        cv2 = self._cv2
        if len(frames) < 2:
            return VisionResult(self.name, 5.0, frames_analyzed=len(frames), reasons=["Poucos quadros para análise"])
        hists, grays, bright = [], [], []
        for f in frames:
            hsv = cv2.cvtColor(f.image, cv2.COLOR_BGR2HSV)
            hist = cv2.calcHist([hsv], [0, 1], None, [32, 32], [0, 180, 0, 256])
            cv2.normalize(hist, hist)
            hists.append(hist)
            g = cv2.cvtColor(cv2.resize(f.image, (160, 90)), cv2.COLOR_BGR2GRAY).astype(np.float32)
            grays.append(g)
            bright.append(float(g.mean()))
        changes = np.array([1.0 - cv2.compareHist(hists[i - 1], hists[i], cv2.HISTCMP_CORREL)
                            for i in range(1, len(frames))])
        motion = np.array([float(np.mean(np.abs(grays[i] - grays[i - 1]))) for i in range(1, len(frames))])
        # horizontal centroid of the motion (for action-oriented vertical reframing)
        xs = np.linspace(0.0, 1.0, grays[0].shape[1])
        centroids = []
        for i in range(1, len(frames)):
            d = np.abs(grays[i] - grays[i - 1])
            d = np.where(d > max(8.0, float(d.mean()) * 2), d, 0.0)
            col = d.sum(axis=0)
            centroids.append(float((col * xs).sum() / col.sum()) if col.sum() > 0 else 0.5)
        dt = np.array([max(0.05, frames[i].t - frames[i - 1].t) for i in range(1, len(frames))])
        motion_rate = motion / dt
        ts = np.array([frames[i].t for i in range(1, len(frames))])

        cut_thr = max(0.45, float(np.median(changes) + 4 * (np.median(np.abs(changes - np.median(changes))) + 1e-3)))
        cuts = [round(float(t), 2) for t, c in zip(ts, changes) if c >= cut_thr]
        near = np.abs(ts - peak_t) <= 3.0
        base_motion = float(np.median(motion_rate)) + 1e-3
        peak_motion = float(motion_rate[near].max()) if near.any() else 0.0
        motion_ratio = peak_motion / base_motion
        flash = max((abs(bright[i] - bright[i - 1]) for i in range(1, len(bright))
                     if abs(frames[i].t - peak_t) <= 3.0), default=0.0)

        score = 3.0
        reasons = []
        if motion_ratio >= 1.8:
            score += min(3.0, (motion_ratio - 1.0))
            shown = "mais de 10x" if motion_ratio > 10 else f"{motion_ratio:.1f}x"
            reasons.append(f"Movimento na cena {shown} acima do normal no pico")
        near_cuts = [c for c in cuts if abs(c - peak_t) <= 3.0]
        if near_cuts:
            score += min(2.5, 1.2 * len(near_cuts))
            reasons.append("Mudança brusca de cena próxima ao momento")
        if flash >= 25:
            score += 1.5
            reasons.append("Clarão/explosão visual detectado")
        if not reasons:
            reasons.append("Pouca variação visual")
        mpt = float(ts[int(np.argmax(motion_rate))]) if len(ts) else None
        track = self.face_track(frames)
        webcam = self._find_webcam(frames)
        return VisionResult(
            provider=self.name,
            visual_score=round(min(10.0, score), 2),
            scene_changes=cuts,
            motion_peak_t=round(mpt, 2) if mpt is not None else None,
            webcam_region=webcam,
            reasons=reasons,
            frames_analyzed=len(frames),
            extra={"motion_ratio": round(motion_ratio, 2), "flash": round(flash, 1),
                   "motion": [[round(float(t), 2), round(float(m), 2), round(c, 3)]
                              for t, m, c in zip(ts, motion_rate, centroids)],
                   "face_track": track},
        )

    def face_track(self, frames: list[Frame], max_frames: int = 24) -> list[dict]:
        """Per-frame face boxes (normalised), temporally smoothed: the largest face whose
        centre stays close to the previous one is kept, jumps are rejected."""
        if self._face is None:
            return []
        cv2 = self._cv2
        picks = frames[:: max(1, len(frames) // max_frames)][:max_frames]
        out: list[dict] = []
        prev = None
        for f in picks:
            g = cv2.cvtColor(f.image, cv2.COLOR_BGR2GRAY)
            h, w = g.shape
            faces = self._face.detectMultiScale(g, scaleFactor=1.15, minNeighbors=6, minSize=(max(24, h // 20),) * 2)
            boxes = [(x / w, y / h, fw / w, fh / h) for (x, y, fw, fh) in faces]
            if prev is not None and boxes:
                boxes.sort(key=lambda b: abs(b[0] + b[2] / 2 - prev[0] - prev[2] / 2) + abs(b[1] + b[3] / 2 - prev[1] - prev[3] / 2))
                cand = boxes[0]
                if abs(cand[0] - prev[0]) > 0.15 or abs(cand[1] - prev[1]) > 0.15:
                    continue  # implausible jump between samples
            elif boxes:
                cand = max(boxes, key=lambda b: b[2] * b[3])
            else:
                continue
            if prev is not None:  # exponential smoothing
                cand = tuple(0.6 * c + 0.4 * p for c, p in zip(cand, prev))
            prev = cand
            out.append({"t": round(f.t, 2), "x": round(cand[0], 4), "y": round(cand[1], 4),
                        "w": round(cand[2], 4), "h": round(cand[3], 4)})
        return out

    def _find_webcam(self, frames: list[Frame]) -> dict | None:
        cv2 = self._cv2
        if self._face is None:
            return None
        picks = frames[:: max(1, len(frames) // 12)][:12]
        boxes = []
        for f in picks:
            g = cv2.cvtColor(f.image, cv2.COLOR_BGR2GRAY)
            h, w = g.shape
            faces = self._face.detectMultiScale(g, scaleFactor=1.15, minNeighbors=6, minSize=(max(24, h // 20),) * 2)
            for (x, y, fw, fh) in faces:
                if fh < 0.35 * h:  # overlay webcams show small faces
                    boxes.append((x / w, y / h, fw / w, fh / h))
        if len(boxes) < max(3, int(0.4 * len(picks))):
            return None
        arr = np.array(boxes)
        med = np.median(arr, axis=0)
        close = arr[np.all(np.abs(arr[:, :2] - med[:2]) < 0.05, axis=1)]
        if len(close) < max(3, int(0.4 * len(picks))):
            return None
        x, y, fw, fh = np.median(close, axis=0)
        cx, cy = x + fw / 2, y + fh / 2
        rw, rh = min(1.0, fw * 3.2), min(1.0, fh * 3.0)
        rx = float(min(max(0.0, cx - rw / 2), 1.0 - rw))
        ry = float(min(max(0.0, cy - rh / 2), 1.0 - rh))
        return {"x": round(rx, 4), "y": round(ry, 4), "w": round(float(rw), 4), "h": round(float(rh), 4),
                "confidence": round(len(close) / len(picks), 2)}


class OpenVINOVLMProvider:
    """Experimental VLM provider (Intel Arc via OpenVINO GenAI). Heuristics still run first
    so scene/motion/webcam data is always available; the VLM adds a semantic score."""

    name = "openvino-vlm"
    requires_gpu = True

    PROMPT = (
        "You are reviewing a moment from a live gameplay stream for a short-form clip. "
        "Transcript: \"{transcript}\". Look at the frames and answer ONLY with JSON: "
        '{{"interest": 0-10, "event": "short description in Portuguese", "funny": 0-10, "action": 0-10}}'
    )

    def __init__(self, model_dir: Path, device: str = "GPU"):
        import openvino_genai as ov_genai

        self._heuristic = HeuristicVisionProvider()
        self._pipe = ov_genai.VLMPipeline(str(model_dir), device)
        self._model_dir = model_dir
        self._device = device

    def analyze(self, frames: list[Frame], peak_t: float, transcript: str) -> VisionResult:
        import time

        import openvino as ov

        base = self._heuristic.analyze(frames, peak_t, transcript)
        if not frames:
            return base
        near = sorted(frames, key=lambda f: abs(f.t - peak_t))[:4]
        near.sort(key=lambda f: f.t)
        tensors = [ov.Tensor(np.ascontiguousarray(f.image[:, :, ::-1])[None]) for f in near]
        t0 = time.monotonic()
        out = self._pipe.generate(self.PROMPT.format(transcript=transcript[:600]), images=tensors, max_new_tokens=96)
        latency = time.monotonic() - t0
        text = str(out)
        m = re.search(r"\{.*\}", text, re.S)
        parsed = {}
        if m:
            try:
                parsed = json.loads(m.group(0))
            except json.JSONDecodeError:
                parsed = {}
        interest = float(parsed.get("interest", base.visual_score)) if isinstance(parsed, dict) else base.visual_score
        base.provider = self.name
        base.visual_score = round(min(10.0, 0.6 * interest + 0.4 * base.visual_score), 2)
        base.description = str(parsed.get("event", ""))[:300] if isinstance(parsed, dict) else ""
        if base.description:
            base.reasons.insert(0, f"IA visual: {base.description}")
        base.latency_s = round(latency, 2)
        base.extra.update({"vlm_raw": text[:500], "device": self._device})
        return base


_providers: dict[str, VisionProvider] = {}


def get_provider(name: str, model_dir: Path | None = None, device: str = "GPU") -> VisionProvider:
    if name not in _providers:
        if name == "openvino_vlm":
            if not model_dir or not Path(model_dir).exists():
                raise RuntimeError("openvino_vlm provider needs ASC_OPENVINO_MODEL_DIR pointing to an exported model")
            _providers[name] = OpenVINOVLMProvider(Path(model_dir), device)
        else:
            _providers[name] = HeuristicVisionProvider()
    return _providers[name]


def probe_openvino_devices() -> list[str]:
    try:
        import openvino as ov

        return list(ov.Core().available_devices)
    except Exception:
        return []
