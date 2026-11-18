"""Who is talking (Phase 5, stage E): speaker embeddings + per-session diarization.

* Embeddings: WeSpeaker ResNet34 (VoxCeleb, ONNX, CPU) on Kaldi-compatible 80-bin fbank with
  mean normalisation - the same front-end the model was trained with.
* Turns: one embedding per Whisper segment (sentence) of at least ``MIN_TURN_S``.
* Labels: ``streamer`` (close to the streamer's voice profile), ``other:<n>`` (a stable cluster of
  another voice in this session: friends on the radio, NPC/game voices), ``unknown`` (in between:
  never forced), ``overlap`` (two turns at the same time). Loudness is never used.
* The streamer profile starts as a HYPOTHESIS (the voice that talks the most, by speech time) and
  becomes certain only when the owner confirms it in the panel.
"""

from __future__ import annotations

import logging
import math
from pathlib import Path

import numpy as np

log = logging.getLogger(__name__)

SR = 16000
MIN_TURN_S = 0.8
MAX_TURN_S = 8.0
# measured on 12 real clips of 4 streamers (mixed with game audio): same streamer 0.77 avg (min
# 0.66), different streamers 0.26 avg (max 0.53) -> streamer above 0.55, surely someone else below 0.35
STREAMER_SIM = 0.55  # cosine >= -> streamer
OTHER_SIM = 0.35  # cosine <= -> not the streamer
CLUSTER_SIM = 0.50  # join an existing "other" cluster
MAX_CLUSTERS = 12


# ------------------------------------------------------------------ Kaldi-compatible fbank (numpy)
def _mel(f):
    return 1127.0 * np.log(1.0 + f / 700.0)


def _mel_banks(n_bins: int = 80, n_fft: int = 512, low: float = 20.0, high: float = 8000.0) -> np.ndarray:
    n = n_fft // 2  # Kaldi ignores the Nyquist bin
    fft_mel = _mel(np.arange(n) * SR / n_fft)
    lo, hi = _mel(low), _mel(high)
    delta = (hi - lo) / (n_bins + 1)
    banks = np.zeros((n_bins, n), dtype=np.float32)
    for b in range(n_bins):
        left, center, right = lo + b * delta, lo + (b + 1) * delta, lo + (b + 2) * delta
        up = (fft_mel - left) / (center - left)
        down = (right - fft_mel) / (right - center)
        banks[b] = np.maximum(0.0, np.minimum(up, down))
    return banks


_BANKS = _mel_banks()
_WINDOW = (0.5 - 0.5 * np.cos(2 * math.pi * np.arange(400) / 399)) ** 0.85  # povey


def fbank(samples: np.ndarray) -> np.ndarray:
    """(T, 80) log mel filterbank, 25 ms / 10 ms, like torchaudio.compliance.kaldi.fbank (dither 0)."""
    x = np.asarray(samples, dtype=np.float64) * 32768.0  # WeSpeaker scales waveforms to int16 range
    if len(x) < 400:
        return np.zeros((0, 80), dtype=np.float32)
    n = 1 + (len(x) - 400) // 160
    idx = np.arange(400)[None, :] + 160 * np.arange(n)[:, None]
    frames = x[idx]
    frames = frames - frames.mean(axis=1, keepdims=True)  # remove DC offset
    frames = np.concatenate([frames[:, :1] - 0.97 * frames[:, :1],
                             frames[:, 1:] - 0.97 * frames[:, :-1]], axis=1)  # pre-emphasis
    frames = frames * _WINDOW
    spec = np.abs(np.fft.rfft(frames, n=512)) ** 2
    mel = spec[:, :256] @ _BANKS.T
    return np.log(np.maximum(mel, np.finfo(np.float32).eps)).astype(np.float32)


# ------------------------------------------------------------------ embedder
class SpeakerEmbedder:
    def __init__(self, model_dir: Path, threads: int = 1):
        import onnxruntime as ort

        so = ort.SessionOptions()
        so.intra_op_num_threads = threads
        so.inter_op_num_threads = 1
        self.session = ort.InferenceSession(str(model_dir / "voxceleb_resnet34.onnx"), so,
                                            providers=["CPUExecutionProvider"])

    def embed(self, samples: np.ndarray) -> np.ndarray | None:
        feats = fbank(samples[: int(MAX_TURN_S * SR)])
        if len(feats) < 50:  # < 0.5 s of frames: not reliable
            return None
        feats = feats - feats.mean(axis=0, keepdims=True)  # CMN
        emb = self.session.run(None, {"feats": feats[None, :, :]})[0][0]
        norm = float(np.linalg.norm(emb))
        return (emb / norm).astype(np.float32) if norm > 0 else None


_cache: dict[str, SpeakerEmbedder] = {}


def get_embedder(models_dir: str | Path, threads: int = 1) -> SpeakerEmbedder | None:
    d = Path(models_dir) / "wespeaker-resnet34"
    if not (d / "voxceleb_resnet34.onnx").exists():
        return None
    key = str(d)
    if key not in _cache:
        try:
            _cache[key] = SpeakerEmbedder(d, threads=threads)
        except Exception as exc:  # pragma: no cover - missing runtime
            log.warning("speaker model unavailable", extra={"err": str(exc)[:200]})
            return None
    return _cache[key]


def turns_from_segments(samples: np.ndarray, segments: list[tuple[float, float]], embedder: SpeakerEmbedder,
                        offset: float = 0.0) -> list[dict]:
    """One embedding per speech segment (relative seconds -> absolute with ``offset``)."""
    out = []
    for a, b in segments:
        if b - a < MIN_TURN_S:
            continue
        e = embedder.embed(samples[int(a * SR): int(b * SR)])
        if e is not None:
            out.append({"start": round(offset + a, 2), "end": round(offset + b, 2),
                        "emb": [round(float(v), 4) for v in e]})
    return out


# ------------------------------------------------------------------ assignment (pure, used by the API)
def cosine(a, b) -> float:
    a, b = np.asarray(a, dtype=np.float32), np.asarray(b, dtype=np.float32)
    na, nb = float(np.linalg.norm(a)), float(np.linalg.norm(b))
    return float(a @ b / (na * nb)) if na and nb else 0.0


def assign(emb, profile: list | None, clusters: list[dict]) -> tuple[str, float, list[dict]]:
    """Return (label, confidence, updated clusters). ``clusters``: [{id, centroid, n, seconds}]."""
    if profile is not None:
        s = cosine(emb, profile)
        if s >= STREAMER_SIM:
            return "streamer", round(s, 3), clusters
        if s > OTHER_SIM:
            return "unknown", round(1.0 - abs(s - (STREAMER_SIM + OTHER_SIM) / 2) * 2, 3), clusters
    best, best_s = None, -1.0
    for c in clusters:
        s = cosine(emb, c["centroid"])
        if s > best_s:
            best, best_s = c, s
    if best is not None and best_s >= CLUSTER_SIM:
        n = best["n"]
        cen = (np.asarray(best["centroid"]) * n + np.asarray(emb)) / (n + 1)
        cen = cen / (np.linalg.norm(cen) or 1.0)
        best["centroid"] = [round(float(v), 4) for v in cen]
        best["n"] = n + 1
        return f"other:{best['id']}", round(best_s, 3), clusters
    if len(clusters) >= MAX_CLUSTERS:
        # full: drop the weakest one-off group (game sounds, music, a single NPC line) to make room.
        # Seen in production: 12 singletons filled the slots in minutes and every later voice,
        # including the streamer's, became "unknown". Ids are never reused (old labels stay unique).
        singles = [c for c in clusters if c["n"] <= 1]
        if not singles:
            return "unknown", 0.0, clusters
        weakest = min(singles, key=lambda c: (c.get("seconds", 0.0), c["id"]))
        top_id = max(c["id"] for c in clusters)
        clusters = [c for c in clusters if c is not weakest]
        clusters.append({"id": top_id + 1, "centroid": [round(float(v), 4) for v in emb], "n": 1, "seconds": 0.0})
        return f"other:{top_id + 1}", 1.0, clusters
    cid = max((c["id"] for c in clusters), default=0) + 1
    clusters.append({"id": cid, "centroid": [round(float(v), 4) for v in emb], "n": 1, "seconds": 0.0})
    return f"other:{cid}", 1.0, clusters


def overlaps(turns: list[dict], min_s: float = 0.3) -> set[int]:
    """Indexes of turns that overlap another turn by more than ``min_s`` (simultaneous speech)."""
    hit: set[int] = set()
    srt = sorted(range(len(turns)), key=lambda i: turns[i]["start"])
    for a, b in zip(srt, srt[1:]):
        if min(turns[a]["end"], turns[b]["end"]) - turns[b]["start"] > min_s:
            hit.update((a, b))
    return hit


# ------------------------------------------------------------------ worker side: turns from Whisper words
TURN_GAP_S = 0.7  # a pause longer than this starts a new turn
SPLIT_S = 3.0  # turns at least this long are also embedded per half (speaker change inside the turn)


def word_turns(words: list, gap: float = TURN_GAP_S, max_s: float = MAX_TURN_S) -> list[tuple[float, float, str]]:
    """Group ``[start, end, word, ...]`` into turns split at pauses > ``gap`` or at ``max_s``."""
    out: list[tuple[float, float, str]] = []
    cur: list = []
    for w in words:
        if cur and (float(w[0]) - float(cur[-1][1]) > gap or float(w[1]) - float(cur[0][0]) > max_s):
            out.append((float(cur[0][0]), float(cur[-1][1]), " ".join(str(x[2]) for x in cur)))
            cur = []
        cur.append(w)
    if cur:
        out.append((float(cur[0][0]), float(cur[-1][1]), " ".join(str(x[2]) for x in cur)))
    return out


def embed_turns(samples: np.ndarray, words: list, embedder: SpeakerEmbedder) -> list[dict]:
    """Payload for the API: [{start, end, text, emb: [full] or [full, half1, half2]}] (segment-relative)."""
    out = []
    for a, b, text in word_turns(words):
        if b - a < MIN_TURN_S:
            continue
        full = embedder.embed(samples[int(a * SR): int(b * SR)])
        if full is None:
            continue
        embs = [full]
        if b - a >= SPLIT_S:
            m = (a + b) / 2
            h1 = embedder.embed(samples[int(a * SR): int(m * SR)])
            h2 = embedder.embed(samples[int(m * SR): int(b * SR)])
            if h1 is not None and h2 is not None:
                embs += [h1, h2]
        out.append({"start": round(a, 2), "end": round(b, 2), "text": text[:400],
                    "emb": [[round(float(v), 4) for v in e] for e in embs]})
    return out


def classify(emb, profile: list | None, clusters: list[dict]) -> tuple[str, float]:
    """Like :func:`assign` but never creates or moves clusters (used for half-turn checks)."""
    if profile is not None:
        s = cosine(emb, profile)
        if s >= STREAMER_SIM:
            return "streamer", round(s, 3)
        if s > OTHER_SIM:
            return "unknown", 0.0
    best, best_s = None, -1.0
    for c in clusters:
        s = cosine(emb, c["centroid"])
        if s > best_s:
            best, best_s = c, s
    if best is not None and best_s >= CLUSTER_SIM:
        return f"other:{best['id']}", round(best_s, 3)
    return "unknown", 0.0
