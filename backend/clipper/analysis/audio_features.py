"""Cheap, continuous acoustic features (NumPy only).

For each ``hop`` (default 0.25 s) bin we compute:
* ``loudness_db``  - RMS level in dBFS
* ``centroid_hz``  - power-weighted spectral centroid (screams / shouting are brighter)
* ``flux``         - mean positive frame-to-frame level change (onset strength; laughter
                     and excited speech are strongly modulated)
"""

from __future__ import annotations

import wave
from dataclasses import dataclass
from pathlib import Path

import numpy as np

SR = 16000
FRAME = 800  # 50 ms
STEP = 400  # 25 ms
SILENCE_DB = -90.0


@dataclass
class AudioFeatures:
    hop: float
    loudness_db: list[float]
    centroid_hz: list[float]
    flux: list[float]
    duration: float

    def as_dict(self) -> dict:
        return {
            "hop": self.hop,
            "loudness_db": self.loudness_db,
            "centroid_hz": self.centroid_hz,
            "flux": self.flux,
            "duration": self.duration,
        }


def read_wav_mono(path: Path) -> tuple[np.ndarray, int]:
    with wave.open(str(path), "rb") as w:
        sr = w.getframerate()
        n = w.getnframes()
        ch = w.getnchannels()
        sw = w.getsampwidth()
        raw = w.readframes(n)
    if sw != 2:
        raise ValueError("expected 16-bit PCM wav")
    data = np.frombuffer(raw, dtype="<i2").astype(np.float32) / 32768.0
    if ch > 1:
        data = data.reshape(-1, ch).mean(axis=1)
    return data, sr


def compute_features(samples: np.ndarray, sr: int = SR, hop: float = 0.25) -> AudioFeatures:
    duration = len(samples) / float(sr) if sr else 0.0
    if len(samples) < FRAME:
        return AudioFeatures(hop, [], [], [], duration)
    n_frames = 1 + (len(samples) - FRAME) // STEP
    idx = np.arange(FRAME)[None, :] + STEP * np.arange(n_frames)[:, None]
    frames = samples[idx] * np.hanning(FRAME)[None, :].astype(np.float32)

    rms = np.sqrt(np.mean(frames**2, axis=1) + 1e-12)
    frame_db = 20 * np.log10(rms + 1e-9)

    spec = np.abs(np.fft.rfft(frames, axis=1)) ** 2
    freqs = np.fft.rfftfreq(FRAME, 1.0 / sr)
    power = spec.sum(axis=1) + 1e-12
    centroid = (spec * freqs[None, :]).sum(axis=1) / power

    flux = np.concatenate([[0.0], np.maximum(0.0, np.diff(frame_db))])

    frame_t = (np.arange(n_frames) * STEP + FRAME / 2) / sr
    n_bins = max(1, int(np.ceil(duration / hop)))
    bins = np.minimum((frame_t / hop).astype(int), n_bins - 1)

    loud, cent, flx = [], [], []
    for b in range(n_bins):
        m = bins == b
        if not m.any():
            loud.append(SILENCE_DB)
            cent.append(0.0)
            flx.append(0.0)
            continue
        e = float(np.mean(rms[m] ** 2))
        loud.append(round(max(SILENCE_DB, 10 * np.log10(e + 1e-12)), 2))
        w = rms[m] ** 2
        cent.append(round(float((centroid[m] * w).sum() / (w.sum() + 1e-12)), 1))
        flx.append(round(float(flux[m].mean()), 3))
    return AudioFeatures(hop, loud, cent, flx, duration)
