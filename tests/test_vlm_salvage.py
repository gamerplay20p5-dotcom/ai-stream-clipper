"""Recovery of VLM answers cut by the token limit (syntactic only, nothing invented)."""

from __future__ import annotations

from clipper.ai.jsonparse import extract_json, salvage_truncated
from clipper.ai.manager import ModelManager
from clipper.ai.vlm import ANSWER_REGEX, MAX_LABEL, FrameInput, VLMAnalyzer, dedupe_sentences, drop_cut_word

import re

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
    monkeypatch.setattr(a, "_generate", lambda pipe, prompt, tensors, **kw: LOOPING)
    img = np.zeros((90, 160, 3), np.uint8)
    frames = [FrameInput(float(t), img) for t in range(0, 40, 4)]
    res = a.analyze(frames, 20.0, 40.0, (0.0, 40.0), [], [])
    assert res.summary.startswith("Um carro")
    assert any("truncada" in n for n in res.notes)


def test_answer_grammar_bounds_texts_and_requires_scores():
    ok = ('{"summary": "um carro é atingido", "key_event": "batida", "funny": 1, "unexpected": 7.5, '
          '"important": 8, "action": 10, "events": [{"t": 12.0, "label": "batida forte", "importance": 9}], '
          '"setup_start": 0.0, "climax": 27.3, "resolution_end": null, "complete": true, '
          '"preserve_start": 0.0, "preserve_end": 39.3, "reaction_linked": false, "confidence": 0.8}')
    assert re.fullmatch(ANSWER_REGEX, ok)
    assert extract_json(ok)["unexpected"] == 7.5
    assert not re.fullmatch(ANSWER_REGEX, ok.replace("um carro é atingido", "e o carro " * 31))  # > 200 chars
    five = ", ".join(['{"t": 1.0, "label": "x", "importance": 1}'] * 5)
    assert not re.fullmatch(ANSWER_REGEX, ok.replace('{"t": 12.0, "label": "batida forte", "importance": 9}', five))


def test_dedupe_sentences_removes_only_model_repetition():
    loop = ("Um carro é acidentado, o grupo se esconde. O grupo se esconde e evita a polícia. "
            "O grupo se esconde e evita a polícia. O grupo se esconde e evita,")
    assert dedupe_sentences(loop) == "Um carro é acidentado, o grupo se esconde. O grupo se esconde e evita a polícia."
    assert dedupe_sentences("Frase única sem ponto") == "Frase única sem ponto"
    assert dedupe_sentences(None) == ""


def test_text_at_grammar_cap_loses_its_cut_last_word():
    cut = ("início da cena, grupo de pessoas se esconde em um carro quebrado, evita a polí" + "x" * 200)[:MAX_LABEL]
    assert not drop_cut_word(cut, MAX_LABEL).endswith("x")
    assert drop_cut_word("rótulo curto", MAX_LABEL) == "rótulo curto"
