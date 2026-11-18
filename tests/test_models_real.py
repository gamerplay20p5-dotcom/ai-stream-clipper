"""Real model inference (OpenVINO / ONNX). Skipped when the models are not installed.

    ASC_MODELS_DIR=data/models pytest -m models        # ~2-3 minutes on a laptop CPU
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import numpy as np
import pytest

from conftest import FIXTURES, ROOT

pytestmark = pytest.mark.models
MODELS = Path(os.environ.get("ASC_TEST_MODELS_DIR", ROOT / "data" / "models"))
DEVICE = os.environ.get("ASC_TEST_DEVICE", "CPU")


def _need(*names):
    from clipper.ai.registry import MODELS as CATALOGUE

    for n in names:
        p = MODELS / CATALOGUE[n].dirname
        ok = (p / "yamnet.onnx").exists() if n == "yamnet" else (p / "openvino_tokenizer.xml").exists()
        if not ok:
            pytest.skip(f"model {n} not installed in {MODELS}")


@pytest.fixture(autouse=True)
def _models_env(monkeypatch):
    from clipper.config import reset_settings_cache

    monkeypatch.setenv("ASC_MODELS_DIR", str(MODELS))
    reset_settings_cache()
    yield
    reset_settings_cache()


def _pcm(name: str) -> np.ndarray:
    raw = subprocess.run(["ffmpeg", "-loglevel", "error", "-i", str(FIXTURES / name), "-ac", "1", "-ar", "16000",
                          "-f", "s16le", "-"], capture_output=True, check=True).stdout
    return np.frombuffer(raw, "<i2").astype(np.float32) / 32768.0


def test_yamnet_classifies_real_speech_and_silence():
    _need("yamnet")
    from clipper.ai.acoustic import get_classifier

    r = get_classifier(MODELS).classify(_pcm("gameplay_event.mp4"))
    speech = np.array(r["cats"]["speech"])
    assert speech[:30].mean() > 0.8  # calm narration
    silent = speech[int(20.4 / 0.48):int(21.6 / 0.48)]  # 2.2 s pause before the event
    assert silent.max() < 0.3, silent


def test_vlm_returns_a_grounded_validated_analysis():
    _need("qwen3-vl-2b-int4")
    from clipper.ai.manager import ModelManager
    from clipper.ai.vlm import FrameInput, VLMAnalyzer
    from clipper.analysis.vision import sample_frames

    frames = sample_frames(FIXTURES / "chase_crash.mp4", 20.0, 45.0, 35.4, 1.5)
    m = ModelManager(preferred_device=DEVICE, allow_cpu_fallback=True)
    r = VLMAnalyzer(m, "qwen3-vl-2b-int4").analyze([FrameInput(f.t, f.image) for f in frames], 15.4, 25.0, (20.0, 45.0),
                                                   [(14.0, 15.0, "Oh no! I crashed the car!")], [])
    assert r.summary and not r.fallback and r.model == "qwen3-vl-2b-int4"
    assert len(r.frame_times) <= 8 and all(0 <= t <= 25 for t in r.frame_times)
    assert all(min(r.frame_times) - 1.5 <= e.t <= max(r.frame_times) + 1.5 for e in r.events)
    assert "�" not in r.summary
    assert m.stat("qwen3-vl-2b-int4").calls == 1


def test_narrative_model_writes_titles_from_facts():
    _need("qwen3-1.7b-int4")
    from clipper.ai import narrative
    from clipper.ai.manager import ModelManager
    from clipper.ai.schemas import DirectorDecision, Evidence

    d = DirectorDecision(start_abs=14, end_abs=48, peak_abs=35, score=80, base_score=78,
                         evidence=[Evidence(source="transcript", detail="Fala de reação: “oh no”")])
    r = narrative.write(ModelManager(preferred_device="CPU"), "qwen3-1.7b-int4", vision_model=None,
                        transcript="The police are right behind us. Oh no! I crashed the car!", visual=None,
                        decision=d, game="GTA V", fallback_titles=["fallback"])
    assert not r.fallback and r.titles and r.provider.startswith("openvino-llm:qwen3-1.7b-int4")
    assert all("transcri" not in t.lower() for t in r.titles)
    print(json.dumps(r.model_dump(), ensure_ascii=False))
