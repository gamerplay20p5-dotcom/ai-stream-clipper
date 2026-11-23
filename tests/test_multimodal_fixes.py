"""Fixes after the PR #29 homologation (P0 timestamps and confirmation, P1 thresholds, per-channel pilot, backpressure,
hardware encoder watchdog). No model is needed: VLM answers are given as data."""

from __future__ import annotations

import asyncio
import time
from pathlib import Path

import pytest

from clipper.ai.editorial import judge
from clipper.ai.schemas import VisualAnalysis
from clipper.ai.visual_confirm import KINDS, decide, frame_times
from clipper.ai.visual_evidence import confirmed_event_kind, confirmed_occurrence
from clipper.ai.vlm import parse_answer
from clipper.scoring.engine import score_timeline
from clipper.scoring.titles import grounded_metadata
from clipper.settings_store import AISettings

SHOWN = [0.0, 7.0, 8.7, 10.3, 11.0, 11.7, 13.7, 20.0]  # frame times shown to the model (window-relative)
BASE = {"summary": "um carro capota", "key_event": "o carro capota", "funny": 1, "unexpected": 8, "important": 8,
        "action": 8, "confidence": 0.9}


def events_of(answer):
    fields, notes = parse_answer({**BASE, **answer}, SHOWN, 24.0)
    return fields["events"], notes


# ---------------------------------------------------------------- P0: timestamps written by the VLM

def test_correct_timestamp_on_a_shown_frame_is_kept():
    events, _ = events_of({"events": [{"t": 10.3, "label": "o carro capota", "importance": 9}]})
    assert [e.t for e in events] == [10.3]


def test_incorrect_timestamp_outside_the_shown_frames_is_discarded():
    events, notes = events_of({"events": [{"t": 23.9, "label": "o carro capota", "importance": 9}]})
    assert events == [] and any("descartado" in n for n in notes)


def test_missing_timestamp_is_discarded_and_never_defaulted_to_zero():
    events, _ = events_of({"events": [{"label": "o carro capota", "importance": 9}, {"t": None, "label": "x", "importance": 5}]})
    assert events == []


def test_inconsistent_frame_by_frame_narration_is_not_timing_evidence():
    # What the real Qwen3-VL-2B answered in the homologation: one "event" per shown frame, from the first one
    narration = [{"t": t, "label": f"o objeto se move {i}", "importance": 10} for i, t in enumerate(SHOWN[:4])]
    events, notes = events_of({"events": narration})
    assert events == [] and any("quadro a quadro" in n for n in notes)


def test_out_of_order_arc_is_discarded():
    fields, notes = parse_answer({**BASE, "events": [], "setup_start": 13.7, "climax": 10.3, "resolution_end": 11.0}, SHOWN, 24.0)
    assert fields["arc"].setup_start is None and any("fora de ordem" in n for n in notes)


# ---------------------------------------------------------------- P0: occurrence confirmed apart from the exact instant

def answer(event, confidence=0.9):
    return {"image1": "carro na avenida", "image2": "carro virado" if event != "none" else "carro na avenida",
            "event": event, "confidence": confidence}


@pytest.mark.parametrize("event, control, expected", [
    (answer("rollover"), answer("none"), (True, "rollover")),
    (answer("rollover"), answer("collision"), (False, "unknown")),  # sees events everywhere: unreliable
    (answer("none"), answer("none"), (False, "unknown")),
    (answer("rollover", 0.4), answer("none"), (False, "unknown")),
    ({"event": "rollover"}, answer("none"), (False, "unknown")),  # incomplete answer
    ({}, {}, (False, "unknown")),
])
def test_confirmation_needs_an_event_and_a_quiet_control(event, control, expected):
    occurred, kind, reason = decide(event, control)
    assert (occurred, kind) == expected and reason


def test_confirmation_frames_never_leave_the_available_media():
    hyp = {"start": 12.0, "peak": 12.5, "end": 13.0}
    assert frame_times(hyp, 0.0, 30.0) == pytest.approx([10.4, 11.6, 13.6])
    assert frame_times(hyp, 11.0, 30.0) is None  # control frame would be before the source
    assert frame_times(hyp, 0.0, 13.2) is None  # "after" frame would be past the source
    assert "none" in KINDS


def visual(confirmation=None, **fields):
    data = {"provider": "test", "confidence": 0.9, "event_kind": "unknown", "window_abs": (0, 30),
            "frame_times": SHOWN, "events": [{"t": 0.0, "label": "objeto se move", "importance": 10}],
            "action": 8, "important": 8, "unexpected": 6, "confirmation": confirmation, **fields}
    return VisualAnalysis(**data)


def confirmation(occurred=True, kind="rollover", start=12.0, end=13.0):
    return {"occurred": occurred, "kind": kind, "reason": "x", "hypothesis": {"start": start, "peak": start + 0.5, "end": end}}


def test_confirmed_occurrence_uses_the_detector_timing_with_context_and_outcome():
    v = visual(confirmation())
    assert confirmed_occurrence(v, start=8.0, end=20.0)
    assert confirmed_event_kind(v, peak=12.5, start=8.0, end=20.0) == "rollover"
    # No context before the hypothesis, or no outcome after it: not enough
    assert confirmed_event_kind(v, peak=12.5, start=11.0, end=20.0) == "unknown"
    assert confirmed_event_kind(v, peak=12.5, start=8.0, end=13.5) == "unknown"
    # Not confirmed by the pass, or no pass at all
    assert confirmed_event_kind(visual(confirmation(occurred=False)), 12.5, 8.0, 20.0) == "unknown"
    assert confirmed_event_kind(visual(None), 12.5, 8.0, 20.0) == "unknown"
    assert confirmed_event_kind(visual(confirmation(), fallback=True), 12.5, 8.0, 20.0) == "unknown"


def test_titles_name_the_event_only_when_confirmed():
    streamer = {"display_name": "Canal", "registered_game": "GTA V", "platform": "twitch"}
    named = grounded_metadata(streamer, "pt", visual(confirmation()), start=8.0, peak=12.5, end=20.0)
    assert named["event"] == "rollover" and named["title"].startswith("Capotamento")
    neutral = grounded_metadata(streamer, "pt", visual(confirmation(occurred=False)), start=8.0, peak=12.5, end=20.0)
    assert neutral["event"] == "unknown" and neutral["title"].startswith("Trecho da live")


def test_editorial_judgement_sees_context_and_outcome_only_from_a_confirmed_span():
    v = visual(confirmation()).model_dump()
    kwargs = dict(start=8.0, end=20.0, peak=12.5, breakdown={}, kinds=[], visual=v, visual_grounded=True)
    complete = judge(**kwargs, evidence_span=(12.0, 13.0))
    at_edge = judge(**{**kwargs, "start": 11.0}, evidence_span=(12.0, 13.0))
    assert complete.criteria["narrative"].value >= 7.5 and complete.criteria["context"].value >= 7.5
    assert complete.final > at_edge.final


# ---------------------------------------------------------------- P1: thresholds, pilot per channel

def visual_only_timeline(confidence, **settings):
    motion = {"start": 10, "peak": 12, "end": 13, "confidence": confidence, "signal": "abrupt_change"}
    return {"from": 0, "to": 40, "data_start": 0, "scored_until": 0, "final": True,
            "features": [{"start": 0, "extra": {"visual_motion": [motion]}}],
            "settings": {"visual_discovery": "on", "min_score": 70, **settings}}


def test_visual_hypotheses_have_their_own_investigation_threshold():
    investigated = score_timeline(visual_only_timeline(0.64, visual_investigate_min=60))
    assert len(investigated["candidates"]) == 1  # 64 >= 60 although below the editorial min_score of 70
    held = score_timeline(visual_only_timeline(0.64, visual_investigate_min=70))
    assert not held["candidates"] and {r["reason"] for r in held["rejected"]} == {"below_min_score"}


def test_visual_candidates_have_an_hourly_cap():
    clips = [{"start": t, "end": t + 5, "peak": t + 2, "transcript": "", "kinds": [],
              "visual_evidence": [{"start": t, "peak": t + 2, "end": t + 3, "confidence": 0.7, "signal": "rotation"}]}
             for t in (100, 200)]
    tl = visual_only_timeline(0.7, visual_max_per_hour=2)
    tl.update(clips=clips, **{"from": 0})
    tl["features"][0]["extra"]["visual_motion"][0].update(start=300, peak=302, end=303)
    tl["to"] = 330
    capped = score_timeline(tl)
    assert not capped["candidates"] and "rate_limit" in {r["reason"] for r in capped["rejected"]}


def test_channel_pilot_overrides_the_global_flag():
    off = AISettings()
    assert off.visual_discovery == "off"
    assert off.for_channel({"visual_discovery": "shadow"}).visual_discovery == "shadow"
    assert off.for_channel({"visual_discovery": "bogus"}).visual_discovery == "off"
    assert AISettings(visual_discovery="on").for_channel({"visual_discovery": "off"}).visual_discovery == "off"
    assert off.for_channel({}).visual_discovery == "off"


def test_channel_pilot_preference_is_validated(admin):
    from conftest import CSRF

    sid = admin.post("/api/streamers", json={"url": "https://www.twitch.tv/piloto_visual"}, headers=CSRF).json()["id"]
    ok = admin.patch(f"/api/streamers/{sid}", json={"preferences": {"visual_discovery": "shadow"}}, headers=CSRF)
    assert ok.status_code == 200, ok.text
    bad = admin.patch(f"/api/streamers/{sid}", json={"preferences": {"visual_discovery": "always"}}, headers=CSRF)
    assert bad.status_code == 422


async def test_channel_pilot_preserves_legacy_editorial_settings(admin, app):
    import httpx

    from clipper.config import get_settings
    from clipper.db import session_scope
    from clipper.models import AppSetting, Streamer, StreamSession, Worker
    from clipper.workers.client import InternalClient
    from conftest import CSRF

    legacy = {"min_score": 75, "auto_publish": False, "weights": {"surprise": 1.6}, "editor_mode": "off"}
    with session_scope() as db:
        row = db.get(AppSetting, "ai")
        previous = row.value if row is not None else None
        db.merge(AppSetting(key="ai", value=legacy))

    channels = []
    worker_id = "release-pilot-scoring"
    worker = InternalClient("http://testserver", get_settings().internal_secret, worker_id,
                            transport=httpx.ASGITransport(app=app))
    try:
        settings = admin.get("/api/settings").json()
        assert settings["min_score"] == 75 and settings["auto_publish"] is False
        assert settings["weights"]["surprise"] == 1.6 and settings["editor_mode"] == "off"
        assert settings["visual_discovery"] == "off"
        sessions = []
        for name in ("piloto_isolado", "controle_isolado"):
            created = admin.post("/api/streamers", json={"url": f"https://www.twitch.tv/{name}"}, headers=CSRF)
            assert created.status_code == 201, created.text
            channels.append(created.json()["id"])
            with session_scope() as db:
                session = StreamSession(streamer_id=channels[-1])
                db.add(session)
                db.flush()
                sessions.append(session.id)

        assert (await worker.post("/internal/workers/register", {"roles": ["scoring"]}))["roles"] == ["scoring"]
        assert await worker.post("/internal/workers/heartbeat") == {"ok": True}
        pilot_timeline, control_timeline = [f"/internal/sessions/{sid}/timeline" for sid in sessions]
        assert admin.get(pilot_timeline).status_code == 401

        for mode, effective in (("shadow", "shadow"), ("on", "on"), (None, "off")):
            result = admin.patch(f"/api/streamers/{channels[0]}", json={"preferences": {"visual_discovery": mode}},
                                 headers=CSRF)
            assert result.status_code == 200, result.text
            assert admin.get("/api/settings").json() == settings
            pilot = (await worker.get(pilot_timeline))["settings"]
            control = (await worker.get(control_timeline))["settings"]
            assert pilot == {**control, "visual_discovery": effective}
            assert control["visual_discovery"] == "off"
            assert pilot["min_score"] == 75 and pilot["auto_publish"] is False
    finally:
        await worker.aclose()
        with session_scope() as db:
            if previous is not None:
                db.merge(AppSetting(key="ai", value=previous))
            else:
                db.delete(db.get(AppSetting, "ai"))
            for channel in channels:
                db.delete(db.get(Streamer, channel))
            if registered := db.get(Worker, worker_id):
                db.delete(registered)


# ---------------------------------------------------------------- P1: audio worker backpressure

class _Ctx:
    def __init__(self, slots=1, timeout=6.0):
        from types import SimpleNamespace

        self.settings = SimpleNamespace(visual_discovery_slots=slots, visual_discovery_timeout_s=timeout)
        self.payload = {"duration": 10.0}


def test_busy_scan_slots_skip_the_segment_instead_of_queuing(monkeypatch):
    from clipper.analysis import visual_discovery
    from clipper.workers import handlers

    monkeypatch.setattr(handlers, "_POOL", None)
    monkeypatch.setattr(visual_discovery, "discover_visual", lambda *a, **k: (time.sleep(0.5), [])[1])

    async def run():
        first = asyncio.create_task(handlers._discover(_Ctx(slots=1), Path("x.ts"), 1, 0))
        await asyncio.sleep(0.05)
        second = await handlers._discover(_Ctx(slots=1), Path("x.ts"), 1, 1)
        return await first, second

    first, second = asyncio.run(run())
    assert first[1].get("visual_proposals") == 0 and second == ([], {"visual_skipped": "busy"})


def test_scan_over_budget_is_counted_and_never_raises(monkeypatch):
    from clipper.analysis import visual_discovery
    from clipper.workers import handlers

    def slow(*_a, **_k):
        raise visual_discovery.DiscoveryTimeout("over budget")

    monkeypatch.setattr(handlers, "_POOL", None)
    monkeypatch.setattr(visual_discovery, "discover_visual", slow)
    rows, stats = asyncio.run(handlers._discover(_Ctx(), Path("x.ts"), 1, 0))
    assert rows == [] and stats["visual_skipped"] == "timeout"


# ---------------------------------------------------------------- P1: hardware encoder watchdog

def test_stuck_hardware_encoder_falls_back_to_libx264_within_its_budget(monkeypatch, tmp_path):
    from clipper import media
    from clipper.render import renderer

    calls = []
    real_run = media.run

    async def fake_run(args, **kwargs):
        calls.append(("h264_qsv" in args, kwargs.get("timeout")))
        if "h264_qsv" in args:  # the hardware encoder hangs until its watchdog fires
            raise media.MediaError(f"ffmpeg timed out after {kwargs.get('timeout')}s")
        return await real_run(args, **kwargs)

    monkeypatch.setattr(media, "run", fake_run)
    src = Path(__file__).resolve().parent / "fixtures" / "gameplay_event.mp4"
    spec = renderer.RenderSpec(source=src, source_start=0, start=2, end=12, peak=6, words=[], encoder="h264_qsv", hw_timeout=45)
    result = asyncio.run(renderer.render(spec, tmp_path))
    hardware = [t for hw, t in calls if hw]
    assert hardware == [45]  # a short budget, not the software timeout (>= 600 s)
    assert "encoder_fallback:h264_qsv" in result.applied and "encoder:libx264" in result.applied


def test_hardware_output_must_be_a_valid_vertical_h264(tmp_path):
    from clipper import media
    from clipper.render import renderer

    bad = tmp_path / "bad.mp4"
    bad.write_bytes(b"not a video")
    with pytest.raises(media.MediaError):
        renderer._check_output(bad, 10.0)


# ---------------------------------------------------------------- detector: the player's own vehicle

def test_abrupt_change_of_a_large_central_vehicle_is_proposed_and_a_plain_drive_is_not(tmp_path):
    from fixtures.make_realistic_fixtures import write_scene
    from clipper.analysis.visual_discovery import discover_visual

    rollover, driving = tmp_path / "rollover.mp4", tmp_path / "driving.mp4"
    write_scene(rollover, "rollover")
    write_scene(driving, "driving")
    found = discover_visual(rollover, 24.0)
    assert len(found) == 1 and abs(found[0]["peak"] - 12.5) <= 0.5 and found[0]["confidence"] < 0.7
    assert discover_visual(driving, 24.0) == []


def test_scan_stops_at_its_time_budget(tmp_path):
    from fixtures.make_realistic_fixtures import write_scene
    from clipper.analysis.visual_discovery import DiscoveryTimeout, discover_visual

    video = tmp_path / "driving.mp4"
    write_scene(video, "driving")
    with pytest.raises(DiscoveryTimeout):
        discover_visual(video, 24.0, timeout_s=0.01)
