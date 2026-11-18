"""Speech-to-text providers.

``FasterWhisperProvider`` runs CTranslate2 Whisper on CPU (int8) and uses the Silero VAD
bundled with faster-whisper to skip non-speech audio. Providers are swappable through
:func:`get_transcriber`.
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

import numpy as np

log = logging.getLogger(__name__)


@dataclass
class Word:
    start: float
    end: float
    word: str
    probability: float = 1.0


@dataclass
class TranscriptResult:
    words: list[Word] = field(default_factory=list)
    language: str = ""
    speech_ratio: float = 0.0
    text: str = ""


class Transcriber(Protocol):
    name: str

    def transcribe(self, audio: np.ndarray, language: str | None, prompt: str | None = None) -> TranscriptResult:
        """``audio``: mono float32 PCM at 16 kHz."""
        ...


class FasterWhisperProvider:
    def __init__(self, model: str, compute_type: str = "int8", cpu_threads: int = 4, download_root: Path | None = None,
                 workers: int = 1):
        from faster_whisper import WhisperModel

        self.name = f"faster-whisper:{model}"
        self.workers = max(1, int(workers))
        log.info("loading whisper model", extra={"model": model, "compute_type": compute_type, "threads": cpu_threads,
                                                 "workers": self.workers})
        # num_workers: CTranslate2 runs that many transcriptions in parallel on ONE loaded model
        # (cpu_threads each). Before v0.3.1 a lock serialised every call, so the audio worker's
        # concurrency setting never parallelised transcription (found with 3 simultaneous lives).
        self._model = WhisperModel(
            model,
            device="cpu",
            compute_type=compute_type,
            cpu_threads=cpu_threads,
            num_workers=self.workers,
            download_root=str(download_root) if download_root else None,
        )
        self._lock = threading.BoundedSemaphore(self.workers)

    def transcribe(self, audio: np.ndarray, language: str | None, prompt: str | None = None) -> TranscriptResult:
        lang = None if not language or language == "auto" else language
        with self._lock:
            segments, info = self._model.transcribe(
                np.ascontiguousarray(audio, dtype=np.float32),
                language=lang,
                beam_size=3,
                word_timestamps=True,
                vad_filter=True,
                vad_parameters={"min_silence_duration_ms": 400},
                condition_on_previous_text=False,
                initial_prompt=prompt or None,
            )
            words: list[Word] = []
            texts: list[str] = []
            for seg in segments:
                texts.append(seg.text.strip())
                for w in seg.words or []:
                    token = w.word.strip()
                    if token:
                        words.append(Word(round(w.start, 3), round(w.end, 3), token[:120], round(w.probability, 3)))
        duration = float(getattr(info, "duration", 0.0) or 0.0)
        after_vad = float(getattr(info, "duration_after_vad", duration) or 0.0)
        ratio = (after_vad / duration) if duration > 0 else 0.0
        return TranscriptResult(words, info.language or (lang or ""), round(min(1.0, ratio), 3), " ".join(texts))


class NullTranscriber:
    """Used when transcription is disabled; acoustic analysis still runs."""

    name = "none"

    def transcribe(self, audio: np.ndarray, language: str | None, prompt: str | None = None) -> TranscriptResult:
        return TranscriptResult()


_cache: dict[str, Transcriber] = {}
_cache_lock = threading.Lock()


def get_transcriber(model: str, compute_type: str, cpu_threads: int, download_root: Path | None,
                    workers: int = 1) -> Transcriber:
    if model in ("", "none", "off"):
        return NullTranscriber()
    key = f"{model}:{compute_type}:{cpu_threads}:{workers}"
    with _cache_lock:
        if key not in _cache:
            _cache[key] = FasterWhisperProvider(model, compute_type, cpu_threads, download_root, workers)
        return _cache[key]
