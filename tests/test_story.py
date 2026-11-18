"""Phase 5 stage A: story memory, association and editorial judgement (required scenarios)."""

from __future__ import annotations

from clipper.ai.editorial import judge
from clipper.ai.story import MAX_EVENTS, Bin, StoryTracker


def _bin(t0, words="", yam=None, kinds=None):
    toks = words.split()
    ws = [(t0 + 0.5 + i * (9.0 / max(1, len(toks))), w) for i, w in enumerate(toks)]
    return Bin(t0, t0 + 10, ws, dict(yam or {}), list(kinds or []))


def _feed(tr, bins):
    for b in bins:
        tr.feed(b)
    return tr


def test_long_police_chase_is_one_story_with_phases_and_outcome():
    tr = StoryTracker()
    bins = [_bin(0, "cala a boca seu idiota para com isso"), _bin(10, "qual foi mano vai se ferrar")]
    bins += [_bin(t, "") for t in range(20, 120, 10)]  # quiet two minutes
    bins += [_bin(120, "a policia ta vindo atras da gente acelera", {"vehicle": 0.6}),
             _bin(130, "corre corre a viatura ta colada", {"vehicle": 0.7}),
             _bin(140, "vira na curva acelera", {"vehicle": 0.6}),
             _bin(150, "eles ainda tao atras policia", {"vehicle": 0.6}),
             _bin(160, "nao acredito bateu bateu", {"vehicle": 0.4, "game_impact": 0.7, "scream": 0.6}),
             _bin(170, "sai do carro corre a pe esconde", {"vehicle": 0.1}),
             _bin(180, "acho que eles foram embora", {}),
             _bin(190, "despistei kkkkk kkkk", {"laughter": 0.6})]
    _feed(tr, bins)
    chases = [s for s in tr.situations if s.kind == "police_chase"]
    assert len(chases) == 1, [s.kind for s in tr.situations]
    ch = chases[0]
    assert 115 <= ch.start <= 125 and ch.last >= 190
    labels = [e.label for e in ch.events]
    assert "colisão durante a fuga" in labels and "fuga a pé" in labels
    assert ch.outcome in ("escaped", "laughter") and ch.complete
    assert ch.related_to is not None  # linked to the earlier argument
    assert any("colisão" in c for c in ch.changes) and ch.confidence >= 0.5
    assert "Perseguição policial" in ch.summary()
    assert tr.at(165) is ch


def test_just_driving_is_not_a_chase():
    tr = _feed(StoryTracker(), [_bin(t, "vou ali na oficina pegar o carro", {"vehicle": 0.7}) for t in range(0, 120, 10)])
    assert not [s for s in tr.situations if s.kind == "police_chase"]


def test_funny_conversation_without_screams_is_recognised():
    tr = _feed(StoryTracker(), [
        _bin(0, "ai mano voce viu o que ele falou ontem kkkk"),
        _bin(10, "nao acredito que ele fez isso hahaha serio"),
        _bin(20, "kkkkkk para mano para", {"laughter": 0.5}),
        _bin(30, "ta bom ta bom vamos voltar"),
    ])
    funny = [s for s in tr.situations if s.kind == "funny_conversation"]
    assert funny and funny[0].phases["climax"]


def test_loud_explosion_without_reaction_is_noise_not_a_story():
    tr = _feed(StoryTracker(), [_bin(t, "", {"gunfire": 0.8, "game_impact": 0.9}) for t in range(0, 30, 10)])
    assert not tr.situations
    assert tr.noise and "som alto do jogo sem reação" in tr.noise[0][1]
    j = judge(start=0, end=30, peak=10, breakdown={"intensity": 9, "reaction": 6, "audience": 7, "narrative": 4,
                                                   "context": 3, "uniqueness": 7},
              kinds=["loudness_spike"], noise=tr.noise_near(15, 20))
    assert j.criteria["reaction"].value <= 3.0  # loudness is not emotion


def test_scream_without_context_is_flagged():
    tr = _feed(StoryTracker(), [_bin(0, "", {"scream": 0.7})])
    assert not [s for s in tr.situations if s.kind == "scare"]
    assert any(lbl == "grito sem contexto" for _, lbl in tr.noise)
    tr2 = _feed(StoryTracker(), [_bin(0, "que susto meu deus", {"scream": 0.7})])
    assert [s for s in tr2.situations if s.kind == "scare"]


def test_argument_that_escalates_vs_one_that_does_not():
    calm = _feed(StoryTracker(), [_bin(0, "cala a boca idiota"), _bin(10, "para com isso qual foi"),
                                  _bin(20, "")] + [_bin(t, "") for t in range(30, 120, 10)])
    c = [s for s in calm.situations if s.kind == "conflict"][0]
    assert c.status == "closed" and not c.complete and "sem desfecho" in c.summary()
    hot = _feed(StoryTracker(), [_bin(0, "cala a boca idiota"), _bin(10, "vem pra cima qual foi"),
                                 _bin(20, "a policia ta chegando foge acelera", {"vehicle": 0.6}),
                                 _bin(30, "acelera policia atras", {"vehicle": 0.6})])
    c2 = [s for s in hot.situations if s.kind == "conflict"][0]
    assert "a discussão escalou para ação" in c2.changes


def test_memory_is_bounded_on_a_very_long_chase():
    bins = [_bin(t, "policia atras acelera", {"vehicle": 0.6}) for t in range(0, 5000, 10)]
    tr = _feed(StoryTracker(), bins)
    ch = [s for s in tr.situations if s.kind == "police_chase"][0]
    assert len(ch.events) <= MAX_EVENTS and len(ch.summary()) <= 400


def test_insufficient_context_is_explained():
    story = {"kind": "police_chase", "start": 100.0, "last": 200.0, "status": "closed", "summary": "Perseguição",
             "complete": True, "outcome": "escaped", "changes": ["colisão"], "climax_t": 180.0}
    late = judge(start=170, end=200, peak=180, breakdown={"context": 6, "narrative": 6, "audience": 6},
                 kinds=["scream"], story=story)
    assert late.criteria["context"].value <= 4 and "antes do início do corte" in late.needed_context
    assert "começar antes" in " ".join(late.edit_suggestions)
    full = judge(start=99, end=200, peak=180, breakdown={"context": 6, "narrative": 6, "audience": 6},
                 kinds=["scream"], story=story)
    assert full.criteria["context"].value >= 7 and full.preliminary > late.preliminary


def test_weak_candidate_inside_an_unfolding_story_waits_instead_of_being_rejected():
    story = {"kind": "police_chase", "start": 100.0, "last": 150.0, "status": "ongoing", "summary": "Perseguição",
             "complete": False, "outcome": "", "changes": [], "climax_t": None}
    j = judge(start=120, end=150, peak=140, breakdown={"audience": 2, "reaction": 2, "context": 2},
              kinds=["loudness_spike"], story=story, director={"score": 30.0}, min_score=55)
    assert j.waiting_context and j.rejection_reason  # low now, but not judged final


def test_vlm_scores_are_capped():
    base = judge(start=0, end=30, peak=10, breakdown={"humor": 2.0, "audience": 3}, kinds=[])
    hyped = judge(start=0, end=30, peak=10, breakdown={"humor": 2.0, "audience": 3}, kinds=[],
                  visual={"funny": 10, "important": 10, "unexpected": 10, "confidence": 0.9, "summary": "x"},
                  director={"score": 40})
    assert hyped.criteria["humor"].value <= 2.0 + (10 - 2.0) * 0.35 + 0.01  # at most 35% of the gap


def test_police_talk_while_driving_without_any_real_sign_is_discarded():
    tr = StoryTracker()
    bins = [_bin(0, "a policia passou ali na rua", {"vehicle": 0.6}), _bin(10, "vou levar o carro na oficina", {"vehicle": 0.6})]
    bins += [_bin(t, "") for t in range(20, 120, 10)]
    _feed(tr, bins)
    assert not [s for s in tr.situations if s.kind == "police_chase"]  # never confirmed -> discarded
    assert tr.at(10) is None


def test_chase_confirmed_by_urgency_minutes_after_the_first_police_mention():
    tr = StoryTracker()
    bins = [_bin(0, "olha a policia ali", {"vehicle": 0.6})]
    bins += [_bin(t, "dirigindo tranquilo", {"vehicle": 0.6}) for t in range(10, 40, 10)]
    bins += [_bin(40, "corre acelera eles tao atras da gente", {"vehicle": 0.7})]
    _feed(tr, bins)
    ch = [s for s in tr.situations if s.kind == "police_chase"][0]
    assert ch.confirmed and ch.start <= 5 and "perseguição ainda não confirmada" not in ch.uncertain
