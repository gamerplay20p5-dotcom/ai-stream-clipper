"""Scoring engine on a real timeline (features + faster-whisper words of the fixture video)."""

import copy
import json

import numpy as np

from clipper.render.subtitles import SubWord, build_ass, group_words
from clipper.scoring.engine import score_timeline
from conftest import FIXTURES

TL = json.loads((FIXTURES / "timeline_event.json").read_text())
EVENT_T = json.loads((FIXTURES / "gameplay_event.json").read_text())["event_t"]


def test_detects_the_event_with_context_and_reasons():
    res = score_timeline(copy.deepcopy(TL))
    assert len(res["candidates"]) == 1
    c = res["candidates"][0]
    assert EVENT_T - 1 <= c["peak"] <= EVENT_T + 4
    assert c["start"] < EVENT_T - 5, "pre-event context must be kept"
    assert c["end"] > EVENT_T + 5, "the resolution after the event must be kept"
    assert 15 <= c["end"] - c["start"] <= 60
    assert c["score"] >= 55
    kinds = {e["kind"] for e in res["events"]}
    assert {"loudness_spike", "scream", "silence_break", "keyword"} <= kinds
    assert any("dB" in r for r in c["reasons"])
    assert "no way" in c["transcript"].lower()
    assert c["title"] and c["hashtags"][0] == "#shorts"
    # boundaries never cut a word in half
    for w in TL["words"]:
        assert not (w[0] < c["start"] < w[1] - 0.05)
        assert not (w[0] + 0.05 < c["end"] < w[1])


def test_deduplicates_against_existing_clip():
    first = score_timeline(copy.deepcopy(TL))["candidates"][0]
    tl = copy.deepcopy(TL)
    tl["clips"] = [{"id": 1, "start": first["start"], "end": first["end"], "peak": first["peak"],
                    "transcript": first["transcript"], "kinds": first["kinds"]}]
    assert score_timeline(tl)["candidates"] == []


def test_calm_conversation_is_not_a_clip():
    tl = copy.deepcopy(TL)
    rng = np.random.default_rng(1)
    for f in tl["features"]:
        n = len(f["loudness_db"])
        f["loudness_db"] = list(-30 + rng.normal(0, 1.0, n))
        f["centroid_hz"] = list(250 + rng.normal(0, 20, n))
        f["flux"] = list(np.abs(rng.normal(0, 0.3, n)))
    tl["words"] = [w for w in tl["words"] if not (21 < w[0] < 31)]
    assert score_timeline(tl)["candidates"] == []


def test_incremental_scoring_waits_for_post_context():
    tl = copy.deepcopy(TL)
    tl["final"] = False
    tl["to"] = EVENT_T + 3  # event just happened, resolution not analysed yet
    res = score_timeline(tl)
    assert res["candidates"] == []
    assert res["scored_until"] <= EVENT_T + 3 - tl["settings"]["post_context_seconds"] + 1e-6


def test_sensitivity_changes_threshold():
    tl = copy.deepcopy(TL)
    tl["settings"]["min_score"] = 99.9
    assert score_timeline(tl)["candidates"] == []


def test_subtitle_grouping_and_escaping():
    words = [SubWord(0.0, 0.3, "Oh"), SubWord(0.3, 0.6, "my"), SubWord(0.6, 1.0, "god!"),
             SubWord(2.0, 2.4, "no"), SubWord(2.4, 2.8, "{way}\\")]
    groups = group_words(words)
    assert [len(g) for g in groups] == [3, 2]
    ass = build_ass(words, style="bold", title="Título {x}", duration=5)
    assert "PlayResX: 1080" in ass and "PlayResY: 1920" in ass
    assert "{way}" not in ass and "(WAY)" in ass
    assert ass.count("Dialogue: 0,") == 5  # one event per active word
    assert "Title,,0,0,0,,Título x" in ass  # braces/emoji never reach the burned title
