from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from clipper.ai import director, edit_plan, structure
from clipper.ai.schemas import VisualAnalysis
from clipper.ai.visual_evidence import webcam_reaction
from clipper.api.routes_internal import AnalysisIn
from clipper.db import session_scope
from clipper.models import Clip, Segment, Streamer, StreamSession
from clipper.scoring.engine import score_timeline
from clipper.scoring.titles import grounded_metadata
from conftest import sign_headers


def motion(start=10, peak=12, end=15):
    return {"start": start, "peak": peak, "end": end, "confidence": 0.73, "signal": "rotation"}


def timeline(mode, events, **overrides):
    return {"from": 0, "to": 40, "data_start": 0, "scored_until": 0, "final": True,
            "features": [{"start": 0, "extra": {"visual_motion": events}}],
            "settings": {"visual_discovery": mode, "multimodal_scoring": True, "grounded_metadata": True},
            **overrides}


def semantic_event(confidence=0.9, label="Veículo capota"):
    return VisualAnalysis(provider="local-test-double", confidence=confidence, event_kind="rollover",
                          events=[{"t": 12, "label": label, "importance": 8}],
                          window_abs=(0, 24), frame_times=[2, 9, 11, 12, 13, 18, 21],
                          arc={"setup_start": 9, "climax": 12, "resolution_end": 18, "complete": True},
                          unexpected=8, action=8, reaction_linked=False)


def test_scoring_waits_for_aftermath_and_keeps_nearby_distinct_events():
    deferred = score_timeline(timeline("on", [motion(end=16)], final=False, to=22))
    assert not deferred["candidates"] and deferred["scored_until"] == 12
    ready = score_timeline(timeline("on", [motion(end=16), motion(19, 22, 24)], to=38,
                                    scored_until=deferred["scored_until"]))
    assert [candidate["peak"] for candidate in ready["candidates"]] == [12, 22]
    assert ready["candidates"][0]["end"] >= 24
    assert ready["candidates"][0]["visual_evidence"][0]["end"] == 16
    again = score_timeline(timeline("on", [motion(end=16), motion(19, 22, 24)], clips=ready["candidates"]))
    assert not again["candidates"] and {r["reason"] for r in again["rejected"]} == {"duplicate"}


def test_shadow_preserves_results_and_reports_visual_candidates():
    on = score_timeline(timeline("shadow", [motion()]))
    baseline = score_timeline(timeline("off", [motion()], settings={}))
    assert {k: v for k, v in on.items() if k != "visual_shadow"} == baseline
    assert on["visual_shadow"]["baseline_candidates"] == 0
    assert on["visual_shadow"]["multimodal_candidates"] == 1


@pytest.mark.parametrize("fields", [{"confidence": float("nan")}, {"start": 13}, {"end": 11},
                                     {"signal": "confirmed_crash"}])
def test_segment_contract_rejects_invalid_motion(fields):
    with pytest.raises(ValidationError):
        AnalysisIn(segment_idx=0, visual_motion=[{**motion(), **fields}])


def test_grounded_titles_reject_unmatched_labels_and_keep_channel_identity():
    streamer = {"display_name": "Lia", "channel": "liaruns", "registered_game": "GTA V", "platform": "twitch"}
    kwargs = dict(start=5, peak=12, end=18)
    assert grounded_metadata(streamer, "pt-BR", semantic_event(), **kwargs)["title"] == "Capotamento em GTA V | Lia"
    assert grounded_metadata(streamer, "en-US", semantic_event(), **kwargs)["title"] == "Vehicle rollover in GTA V | Lia"
    for uncertain in [semantic_event(confidence=0.6), semantic_event(label="Carro anda na pista"),
                      semantic_event(label="Carro segue sem capotar"),
                      semantic_event().model_copy(update={"frame_times": [2, 5, 16, 18]})]:
        assert grounded_metadata(streamer, "en", uncertain, **kwargs)["event"] == "unknown"
    meta = grounded_metadata({"display_name": "Lia", "game": "GTA V"}, "pt")
    assert "GTA" not in meta["title"] and meta["hashtags"] == ["#Lia"]


def test_webcam_sustained_observation_changes_framing_and_preserves_outcome():
    track = [{"t": t, "x": x, "y": .5, "w": .1, "h": .1}
             for t, x in [(8, .1), (10, .1), (11, .1), (12.5, .13), (13, .145)]]
    assert webcam_reaction(track[:-1], 12) is None
    assert webcam_reaction(track[:3] + [{**track[-2], "t": 15}, {**track[-1], "t": 16}], 12)
    reaction = webcam_reaction(track, 12)
    assert reaction == {"start": 12.5, "end": 13, "signal": "head"}
    mouth = [{**f, "x": .1, "mouth": .1 if f["t"] < 12 else .4} for f in track]
    assert webcam_reaction(mouth, 12)["signal"] == "lower_face"
    cand = director.Candidate(5, 15, 12, 70, visual_evidence=[motion()])
    game_audio = director.AcousticTrack([(12, {"game_impact": .9})])
    decision = director.decide(cand, [], game_audio, semantic_event(), director.Limits(8, 30, 0, 24, 55),
                               webcam_reaction=reaction)
    assert decision.keep and decision.end_abs >= 19
    plan = edit_plan.build(decision, webcam_region={"x": 0, "y": 0, "w": .2, "h": .2}, webcam_reaction=reaction)
    assert plan.layout == "split" and plan.webcam_height_pct == 42
    short = structure.Moment(start=5, end=22, peak=12, words=[], media=[(0, 24)], visual_context=[(9, 18)])
    assert not structure.build("E", short).applicable


def test_api_persists_grounded_metadata_from_registered_channel(client, monkeypatch):
    from clipper import pipeline_ai
    from clipper.settings_store import AISettings

    settings = AISettings(editor_mode="off", deep_review_enabled=False)
    monkeypatch.setattr(pipeline_ai, "load_ai_settings", lambda _: settings)
    with session_scope() as db:
        streamer = Streamer(platform="twitch", channel="lia_clip_test", display_name="Lia",
                            url="https://www.twitch.tv/lia_clip_test", preferences={"game": "GTA V", "language": "en"})
        db.add(streamer)
        db.flush()
        session = StreamSession(streamer_id=streamer.id)
        db.add(session)
        db.flush()
        db.add(Segment(session_id=session.id, idx=0, start=0, duration=24, available=True))
        clip = Clip(session_id=session.id, streamer_id=streamer.id, start=3, end=21, peak=12, score=70,
                    status="analyzing", vision_state="pending", kinds=["visual_motion"],
                    provenance={"visual_discovery": [motion()]})
        db.add(clip)
        db.flush()
        cid = clip.id

    visual = semantic_event()
    decision = director.decide(director.Candidate(3, 21, 12, 70, visual_evidence=[motion()]), [],
                               director.AcousticTrack([]), visual, director.Limits(8, 30, 0, 24, 55))
    plan = edit_plan.build(decision, title="Untrusted title")
    body = json.dumps({"decision": decision.model_dump(), "visual": visual.model_dump(),
                       "edit_plan": plan.model_dump(),
                       "narrative": {"provider": "fake", "titles": ["Other person crashes in GTA VI"]}}).encode()
    path = f"/internal/clips/{cid}/analysis"
    response = client.post(path, content=body, headers={**sign_headers("POST", path, body),
                                                        "Content-Type": "application/json"})
    assert response.status_code == 200 and response.json()["ok"]
    with session_scope() as db:
        clip = db.get(Clip, cid)
        assert clip.title == "Vehicle rollover in GTA V | Lia"
        assert clip.edit_plan["title"] == clip.title
        assert "GTA V during Lia's stream" in clip.description
        assert clip.provenance["metadata"]["event"] == "rollover"
        assert clip.status == "rendering"
        assert clip.gate in ("READY", "HUMAN_REVIEW")
