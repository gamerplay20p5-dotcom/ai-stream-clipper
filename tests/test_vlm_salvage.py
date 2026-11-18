"""Recovery of VLM answers cut by the token limit (syntactic only, nothing invented)."""

from __future__ import annotations

from clipper.ai.jsonparse import extract_json, salvage_truncated
from clipper.ai.manager import ModelManager
from clipper.ai.vlm import FrameInput, VLMAnalyzer

import numpy as np

LOOPING = ('```json\n{"summary": "Um carro é atingido e o jogador se esconde", "key_event": "batida",\n'
           '"funny": 2, "unexpected": 7, "important": 8, "action": 9,\n'
           '"events": [{"t": 0.0, "label": "perseguição"}, {"t": 12.0, "label": "carro desliza"}, '
           '{"t": 24.0, "label": "carro desliza"}, {"t": 29.0, "label": "carro des')


def test_salvage_cuts_at_last_complete_value_and_closes_brackets():
    assert extract_json(LOOPING) is None
    obj = salvage_truncated(LOOPING)
    assert obj["summary"].startswith("Um carro")
    assert obj["action"] == 9
    assert [e["t"] for e in obj["events"]] == [0.0, 12.0, 24.0]  # the cut event is dropped, not guessed


def test_salvage_ignores_complete_or_garbage_text():
    assert salvage_truncated('{"a": 1}') is None  # balanced: not a truncation
    assert salvage_truncated("sem json aqui") is None
    assert salvage_truncated('{"summary": "texto cortado no meio') is None  # nothing complete


class _FakeManager(ModelManager):
    def __init__(self):
        super().__init__(preferred_device="CPU")

    def acquire(self, key):
        return object(), "GPU"


def test_analyzer_uses_salvaged_answer_and_says_so(monkeypatch):
    a = VLMAnalyzer(_FakeManager(), "qwen3-vl-2b-int4")
    monkeypatch.setattr(a, "_generate", lambda pipe, prompt, tensors: LOOPING)
    img = np.zeros((90, 160, 3), np.uint8)
    frames = [FrameInput(float(t), img) for t in range(0, 40, 4)]
    res = a.analyze(frames, 20.0, 40.0, (0.0, 40.0), [], [])
    assert res.summary.startswith("Um carro")
    assert any("truncada" in n for n in res.notes)
