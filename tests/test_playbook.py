"""Phase 7: the editorial playbook turns professional editing knowledge into measurable checks."""

from __future__ import annotations

from clipper.ai import playbook
from clipper.ai import quality_gate as qg
from clipper.ai.editorial import LABELS, judge
from clipper.evaluation.playbook_eval import auc, evaluate


def W(text: str, t0: float = 0.0, step: float = 0.5) -> list:
    """Timed words from a sentence (session time)."""
    return [[t0 + i * step, t0 + i * step + 0.4, w] for i, w in enumerate(text.split())]


NITRO = W("abre as configurações do discord nitro trocar de plano pagar com cartão assinatura "
          "vou mostrar pra vocês aqui no canal como faz a assinatura do nitro configurações da conta")
CHASE = W("corre corre a polícia tá vindo vai vai vai entra no beco segura segura") + \
    W("consegui escapei da polícia não acredito kkkk", t0=14.0)


def test_off_game_talk_is_flagged_and_in_game_action_is_not():
    off = playbook.assess(words=NITRO, start=0, end=20, peak=8, kinds=["keyword", "loudness_spike"])
    assert off.offgame and off.value("ingame") < 4
    assert any("discord" in e or "nitro" in e for e in off.criteria["ingame"][1])
    game = playbook.assess(words=CHASE, start=0, end=20, peak=12, kinds=["scream"])
    assert not game.offgame and game.value("ingame") >= 9


def test_payoff_needs_a_resolution_after_the_peak():
    with_payoff = playbook.assess(words=CHASE, start=0, end=20, peak=12)
    assert with_payoff.value("payoff") >= 7
    flat = playbook.assess(words=W("olha a física da água muito bonita essa água olha que da hora mano"), start=0,
                           end=12, peak=4)
    assert flat.value("payoff") == 3.0  # energy without an outcome is filler
    story = playbook.assess(words=[], start=0, end=30, peak=20, story={"outcome": "arrested"})
    assert story.value("payoff") == 9.0  # the story memory knows how it ended


def test_mixed_language_hurts_intelligibility():
    en = playbook.assess(words=W("yeah but anyways i could do it you know what i mean the treasure is so cool"),
                         start=0, end=10, peak=5)
    pt = playbook.assess(words=CHASE, start=0, end=20, peak=12)
    assert en.value("intelligible") < 5 < pt.value("intelligible")


def test_slow_opening_and_long_tail_become_edit_fixes_not_penalties():
    words = W("polícia corre corre", t0=10.0) + W("consegui", t0=14.0) + W("bom então tipo assim né a gente vai lá depois "
                                                                        "e tal mais tarde vamos ver ok beleza", t0=16.0)
    c = playbook.assess(words=words, start=0, end=30, peak=13)
    assert c.value("hook") < 5 and c.value("ending") < 6
    kinds = {f["kind"]: f for f in playbook.edit_fixes(c, start=0, end=30, peak=13, words=words)}
    assert kinds["hook"]["start"] >= 8.5  # start near the action, not at the silent opening
    assert kinds["tail"]["end"] <= 17.0  # end right after the reaction
    assert "hook" not in playbook.SCORE_KEYS and "ending" not in playbook.SCORE_KEYS  # cut issues never reject


def test_adjust_never_raises_a_score_and_cuts_off_game_hard():
    game = playbook.assess(words=CHASE, start=0, end=20, peak=12)
    off = playbook.assess(words=NITRO, start=0, end=20, peak=8)
    assert playbook.adjust(80.0, game) <= 80.0
    assert playbook.adjust(80.0, game) >= 78.0
    assert playbook.adjust(85.0, off) <= 62.0


def test_judgement_shows_the_playbook_and_explains_off_game_rejection():
    off = playbook.assess(words=NITRO, start=0, end=20, peak=8)
    j = judge(start=0, end=20, peak=8, breakdown={"audience": 7, "narrative": 6}, kinds=["keyword"], craft=off,
              fixes=[{"kind": "hook", "detail": "começar mais perto da ação"}])
    d = j.as_dict()
    assert d["criteria"]["ingame"]["label"] == LABELS["ingame"] and d["criteria"]["ingame"]["value"] < 4
    assert "fora do jogo" in j.rejection_reason
    assert "começar mais perto da ação" in j.edit_suggestions
    same = judge(start=0, end=20, peak=8, breakdown={"audience": 7, "narrative": 6}, kinds=["keyword"])
    assert j.preliminary < same.preliminary


def test_quality_gate_blocks_off_game_content():
    def J(ingame):
        base = {"interest": 7, "surprise": 6, "reaction": 7, "originality": 6, "context": 7, "narrative": 7,
                "standalone": 7, "retention": 7, "audio": 7, "redundancy": 10, "ingame": ingame, "payoff": 7}
        return {"criteria": {k: {"label": LABELS[k], "value": v, "evidence": []} for k, v in base.items()},
                "story": None, "final": 70.0, "waiting_context": False}
    talk = [(t, t + 0.4) for t in [x * 0.8 for x in range(1, 40)]]
    r = qg.evaluate(judgement=J(2.0), start=0, end=30, peak=12, keep=True, words=talk, media=(-300.0, 300.0))
    assert any("fora do jogo" in b for b in r.blocking) and r.outcome != qg.READY
    ok = qg.evaluate(judgement=J(9.5), start=0, end=30, peak=12, keep=True, words=talk, media=(-300.0, 300.0))
    assert not any("fora do jogo" in b for b in ok.blocking)


def test_offline_evaluation_reports_agreement_with_humans():
    rows = [
        {"id": 1, "status": "approved", "score": 80, "start": 0, "end": 20, "peak": 12, "kinds": ["scream"],
         "words": CHASE, "situation": None, "visual": None, "title": "fuga"},
        {"id": 2, "status": "rejected", "score": 85, "start": 0, "end": 20, "peak": 8, "kinds": ["keyword"],
         "words": NITRO, "situation": None, "visual": None, "title": "nitro"},
    ]
    r = evaluate(rows)
    assert r["auc_old"] == 0.0 and r["auc_new"] == 1.0  # the off-game clip no longer outranks the chase
    assert r["offgame"] == {"approved": 0, "rejected": 1}
    assert auc([2, 3], [1]) == 1.0
