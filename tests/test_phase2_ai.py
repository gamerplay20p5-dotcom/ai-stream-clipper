"""Phase 2: schemas, JSON parsing, VLM answer grounding, director, edit plans, routing, renderer."""

from __future__ import annotations

import asyncio
from datetime import timedelta

import numpy as np
import pytest

from clipper.ai import director, edit_plan
from clipper.ai.jsonparse import extract_json, fix_text
from clipper.ai.schemas import DirectorDecision, EditPlan, NarrativeArc, VisualAnalysis, VisualEvent
from clipper.ai.vlm import FrameInput, parse_answer, select_frames
from clipper.db import utcnow
from clipper.models import Job, Worker
from clipper.routing import vision_acceptor
from clipper.scoring.engine import Word, build_grid, detect_events, score_timeline
from conftest import FIXTURES

# ---------------------------------------------------------------------------- parsing


def test_extract_json_handles_fences_thinking_trailing_commas_and_broken_utf8():
    raw = "<think>pensando...</think>```json\n{\"a\": \"v�­deo\", \"b\": [1, 2,],}\n```"
    assert extract_json(raw) == {"a": "vídeo", "b": [1, 2]}
    assert extract_json("sem json aqui") is None
    assert extract_json('{"x": "chave } dentro", "y": {"z": 1}} resto') == {"x": "chave } dentro", "y": {"z": 1}}
    assert fix_text("in�­cio") == "início"


def test_vlm_answer_is_grounded_in_shown_frames():
    shown = [2.0, 4.0, 10.0, 11.0, 12.0, 20.0]
    obj = {"summary": "batida", "key_event": "carro bate", "funny": 2, "unexpected": 11, "important": 9, "action": "8",
           "events": [{"t": 11.2, "label": "batida", "importance": 9},
                      {"t": 55.0, "label": "inventado fora da janela", "importance": 10},
                      {"t": "x", "label": "sem tempo"}],
           "setup_start": 3.0, "climax": 11.0, "resolution_end": 19.0, "complete": True,
           "preserve_start": 9, "preserve_end": 14, "reaction_linked": True, "confidence": 1.4}
    fields, notes = parse_answer(obj, shown, span=25.0)
    assert [e.t for e in fields["events"]] == [11.2]
    assert fields["unexpected"] == 10 and fields["action"] == 8.0 and fields["confidence"] == 1
    assert fields["arc"].climax == 11.0 and fields["preserve"] == (9.0, 14.0)
    assert len(notes) == 2
    disordered, notes2 = parse_answer({**obj, "setup_start": 15, "climax": 5}, shown, 25.0)
    assert disordered["arc"].setup_start is None and any("fora de ordem" in n for n in notes2)


def test_visual_analysis_schema_rejects_events_outside_window():
    with pytest.raises(ValueError):
        VisualAnalysis(provider="x", window_abs=(10, 20), events=[VisualEvent(t=30, label="fora", importance=5)])
    with pytest.raises(ValueError):
        NarrativeArc(setup_start=10, climax=5)


def test_frame_selection_keeps_peak_dense_and_context_spread():
    frames = [FrameInput(float(t), np.zeros((4, 4, 3), np.uint8)) for t in range(0, 40)]
    chosen = select_frames(frames, peak_t=30.0, max_frames=8)
    ts = [f.t for f in chosen]
    assert ts == sorted(ts) and len(ts) == 8
    assert sum(1 for t in ts if abs(t - 30) <= 2) >= 3  # dense around the peak
    assert min(ts) <= 5 and max(ts) >= 38  # but the window edges are represented


# ---------------------------------------------------------------------------- director


def _words(*items):
    return [(s, s + 0.3, w) for s, w in items]


LIMITS = director.Limits(min_len=15, max_len=60, avail_start=0, avail_end=120, min_score=55)


def _cand(score=70.0, start=20.0, end=40.0, peak=30.0):
    return director.Candidate(start, end, peak, score, {}, ["Pico de volume"], ["loudness_spike"])


def test_director_audio_only_keeps_heuristic_bounds_without_extra_evidence():
    words = _words((19.5, "okay"), (25.0, "we"), (31.0, "no"), (31.4, "way!"), (39.8, "fine."))
    d = director.decide(_cand(), words, director.AcousticTrack(), None, LIMITS)
    assert d.mode == "audio_only" and d.keep
    assert 15 <= d.start_abs <= 21 and 39 <= d.end_abs <= 42


def test_multimodal_visual_context_moves_the_start_earlier():
    """Criterion 3: the same audio + a different visual analysis changes the clip."""
    words = _words((31.0, "no"), (31.4, "way!"))
    vis = VisualAnalysis(provider="vlm", model="m", window_abs=(5.0, 50.0), summary="perseguição termina em batida",
                         key_event="batida", unexpected=9, action=8, confidence=0.9,
                         events=[VisualEvent(t=25.0, label="batida", importance=9)],
                         arc=NarrativeArc(setup_start=4.0, climax=25.0, resolution_end=40.0, complete=True))
    d_audio = director.decide(_cand(), words, director.AcousticTrack(), None, LIMITS)
    d_multi = director.decide(_cand(), words, director.AcousticTrack(), vis, LIMITS)
    assert d_multi.mode == "multimodal"
    assert d_multi.start_abs <= 8.0 < d_audio.start_abs  # setup at 5 + 4 = 9 s (minus margin)
    assert d_multi.end_abs >= 46.0  # resolution at 45 s kept
    assert any(e.source == "visual" for e in d_multi.evidence)
    assert any("contexto visual" in a for a in d_multi.adjustments)


def test_unconfirmed_visual_opinion_cannot_raise_the_score():
    words = _words((31.0, "okay"))
    hype = VisualAnalysis(provider="vlm", model="m", window_abs=(10.0, 50.0), unexpected=10, action=10, confidence=1)
    d = director.decide(_cand(score=50), words, director.AcousticTrack(), hype, LIMITS)
    assert d.score <= 50 and any("não aumenta" in a for a in d.adjustments)
    confirmed = hype.model_copy(update={"events": [VisualEvent(t=20.0, label="explosão", importance=9)]})
    d2 = director.decide(_cand(score=50), words, director.AcousticTrack(), confirmed, LIMITS)
    assert d2.score > d.score


def test_game_sound_without_reaction_is_discarded():
    frames = [(30.0 + k * 0.5, {"game_impact": 0.8, "speech": 0.6}) for k in range(4)]
    d = director.decide(_cand(score=70), _words((29.0, "vou"), (29.5, "comprar")),
                        director.AcousticTrack(frames), None, LIMITS)
    assert not d.keep and d.score < 55
    assert any("vem do jogo" in a for a in d.adjustments)
    react = frames + [(31.0, {"laughter": 0.9})]
    d2 = director.decide(_cand(score=70), [], director.AcousticTrack(react), None, LIMITS)
    assert d2.keep and any("risada" in e.detail for e in d2.evidence)


def test_director_extends_end_when_action_continues_and_respects_max_length():
    motion = [(t, 50.0 if 38 <= t <= 42 else 5.0) for t in np.arange(10, 60, 0.5)]
    d = director.decide(_cand(end=40.0), [], director.AcousticTrack(), None, LIMITS, motion=motion)
    assert d.end_abs > 41 and any("meio da ação" in a for a in d.adjustments)
    short = director.Limits(min_len=10, max_len=18, avail_start=0, avail_end=120, min_score=55)
    vis = VisualAnalysis(provider="vlm", model="m", window_abs=(0, 60), unexpected=8, confidence=1,
                         events=[VisualEvent(t=30, label="x", importance=8)],
                         arc=NarrativeArc(setup_start=2.0, climax=30.0, resolution_end=50.0))
    d2 = director.decide(_cand(), [], director.AcousticTrack(), vis, short)
    assert d2.end_abs - d2.start_abs <= 18.01
    assert d2.start_abs <= 30.0 <= d2.end_abs  # the event itself is never trimmed away


def test_tension_onset_finds_the_start_of_the_situation_but_not_old_mentions():
    chase = [Word(14.8, 15.0, "Wait,"), Word(15.4, 15.7, "the"), Word(15.8, 16.0, "police"), Word(16.4, 16.6, "behind"),
             Word(16.8, 17.0, "us,"), Word(17.2, 17.4, "go,"), Word(17.6, 17.8, "go,"), Word(18.1, 18.3, "go."),
             Word(23.0, 23.2, "helicopter"), Word(28.4, 28.7, "keep"), Word(28.8, 29.0, "going."),
             Word(30.5, 30.7, "lose"), Word(30.8, 31.0, "them")]
    onset = director.tension_onset(chase, peak=35.4)
    assert onset is not None and abs(onset[0] - 14.8) < 0.01
    old_mention = [Word(3.0, 3.2, "police"), Word(4.0, 4.2, "caught"), Word(10.0, 10.3, "okay"), Word(18.0, 18.2, "fine")]
    assert director.tension_onset(old_mention, peak=24.0) is None


# ---------------------------------------------------------------------------- edit plan


def _decision(score=80.0):
    return DirectorDecision(start_abs=100.0, end_abs=130.0, peak_abs=115.0, score=score, base_score=score)


def test_edit_plan_layouts_and_moderate_zoom():
    webcam = {"x": 0.7, "y": 0.0, "w": 0.3, "h": 0.3}
    p = edit_plan.build(_decision(), webcam_region=webcam)
    assert p.layout == "split" and p.zooms and 1.0 < p.zooms[0].scale <= 1.35
    assert abs(p.zooms[0].t - 14.85) < 0.01  # clip-relative, just before the peak
    concentrated = [(100 + t, 0.62 + 0.01 * np.sin(t)) for t in np.arange(0, 30, 1.0)]
    p2 = edit_plan.build(_decision(), motion_cx=concentrated)
    assert p2.layout == "action" and 2 <= len(p2.crop_keyframes) <= 12
    xs = [k.cx for k in p2.crop_keyframes]
    assert all(0.2 <= x <= 0.8 for x in xs)
    spread = [(100 + t, (t % 2) * 0.8 + 0.1) for t in np.arange(0, 30, 1.0)]
    assert edit_plan.build(_decision(), motion_cx=spread).layout == "gameplay"  # no info lost
    assert edit_plan.build(_decision(), template_key="minimalista").zooms == []
    assert edit_plan.build(_decision(score=50)).zooms == []  # no punch-in on weak moments
    assert edit_plan.build(_decision(), layout_override="zoom").layout == "zoom"


def test_crop_keyframes_limit_pan_speed():
    jumpy = [(0.0, 0.2), (1.0, 0.8), (2.0, 0.2), (3.0, 0.8)]
    kf = edit_plan.smooth_keyframes(jumpy, max_speed=0.25)
    for a, b in zip(kf, kf[1:]):
        assert abs(b.cx - a.cx) <= 0.25 * (b.t - a.t) + 1e-6


# ---------------------------------------------------------------------------- acoustic classes in scoring


def _feat(start, loud, cats):
    n = len(loud)
    return {"start": start, "hop": 0.25, "loudness_db": loud, "centroid_hz": [300.0] * n, "flux": [0.1] * n,
            "extra": {"yamnet": {"hop": 0.48, "cats": cats}}}


def test_uncorroborated_loudness_spike_is_penalised_only_with_acoustic_classes():
    rng = np.random.default_rng(3)
    loud = list(-30 + rng.normal(0, 0.8, 240))
    for i in range(160, 172):  # +18 dB burst at 40 s
        loud[i] = -12.0
    n_frames = 125
    cats_game = {"speech": [0.9] * n_frames, "game_impact": [0.0] * n_frames, "laughter": [0.0] * n_frames}
    for k in range(82, 86):
        cats_game["game_impact"][k] = 0.8
    base = {"from": 0, "to": 60, "data_start": 0, "scored_until": 0, "final": True, "words": [], "clips": [],
            "settings": {"sensitivity": 0.5, "min_score": 0}}
    with_cls = score_timeline({**base, "features": [_feat(0, loud, cats_game)]})
    without_cls = score_timeline({**base, "features": [{k: v for k, v in _feat(0, loud, cats_game).items() if k != "extra"}]})
    s_with = max(c["score"] for c in with_cls["candidates"])
    s_without = max(c["score"] for c in without_cls["candidates"])
    assert s_with < s_without
    assert any("jogo" in r for c in with_cls["candidates"] for r in c["reasons"])
    spikes = [e for e in with_cls["events"] if e["kind"] == "loudness_spike"]
    assert spikes and spikes[0]["data"]["source"] in ("game", "mixed")


def test_yamnet_laughter_becomes_an_event():
    n = 240
    loud = [-30.0] * n
    cats = {"laughter": [0.0] * 125, "speech": [0.5] * 125}
    for k in range(40, 44):
        cats["laughter"][k] = 0.9
    grid = build_grid([_feat(0, loud, cats)], 0, 60)
    evs = detect_events(grid, [], (0, 60), 0.5)
    assert any(e.kind == "laughter" and e.data.get("by") == "yamnet" for e in evs)


# ---------------------------------------------------------------------------- worker selection


def _worker(wid, models, gpu=True, vram=8.0, latency=None, running=0):
    metrics = {"running_jobs": running}
    if latency is not None:
        metrics["ai"] = {"models": {"qwen3-vl-2b-int4": {"avg_latency_s": latency}}}
    return Worker(id=wid, roles=["vision"], capabilities={"gpu": gpu, "ai_models": models, "vram_total_gb": vram,
                                                         "vision_concurrency": 1}, metrics=metrics)


def _job(age_s=0.0, model="qwen3-vl-2b-int4"):
    return Job(id=1, type="vision_analyze", payload={"model": model}, created_at=utcnow() - timedelta(seconds=age_s))


def test_routing_prefers_capable_fast_workers_and_never_stalls():
    w201 = _worker("201-vision", ["qwen3-vl-2b-int4"], latency=9.0)
    w210 = _worker("210-vision", ["qwen3-vl-2b-int4"], latency=3.0)
    w206 = _worker("206-cpu", [], gpu=False)
    online = [w201, w210, w206]
    now = utcnow()
    assert vision_acceptor(w210, online, now)(_job())
    assert not vision_acceptor(w201, online, now)(_job())  # slower: waits
    assert vision_acceptor(w201, online, now)(_job(age_s=30))  # ...unless the job waited too long
    assert not vision_acceptor(w206, online, now)(_job())  # no model: never takes it while holders exist
    busy210 = _worker("210-vision", ["qwen3-vl-2b-int4"], latency=3.0, running=1)
    assert vision_acceptor(w201, [w201, busy210, w206], now)(_job())  # fast one busy -> 201 takes it
    assert vision_acceptor(w206, [w206], now)(_job())  # nobody has the model -> heuristic fallback anywhere
    small = _worker("small-gpu", ["qwen3-vl-4b-int4"], vram=4.0)
    assert vision_acceptor(small, [small, w206], now)(_job(model="qwen3-vl-4b-int4")) is True  # nobody fits -> fallback
    assert not vision_acceptor(w206, [w206, w210], now)(_job()) and vision_acceptor(w210, [w206, w210], now)(_job())


# ---------------------------------------------------------------------------- model manager


def test_model_manager_reports_missing_models(tmp_path, monkeypatch):
    from clipper.ai.manager import ModelManager, ModelUnavailable
    from clipper.config import reset_settings_cache

    monkeypatch.setenv("ASC_MODELS_DIR", str(tmp_path))
    reset_settings_cache()
    try:
        m = ModelManager(preferred_device="CPU")
        with pytest.raises(ModelUnavailable):
            m.acquire("qwen3-vl-2b-int4")
        assert m.stat("qwen3-vl-2b-int4").state == "missing"
        with pytest.raises(ModelUnavailable):
            m.acquire("nao-existe")
    finally:
        monkeypatch.delenv("ASC_MODELS_DIR")
        reset_settings_cache()


def test_narrative_template_fallback():
    from clipper.ai import narrative

    r = narrative.write(None, "qwen3-1.7b-int4", vision_model=None, transcript="x", visual=None, decision=_decision(),
                        game="GTA V", fallback_titles=["Título A", "Título B"])
    assert r.fallback and r.titles == ["Título A", "Título B"]


# ---------------------------------------------------------------------------- renderer executes the plan


def test_piecewise_expression_and_linear_gain():
    from clipper.render.renderer import linear_gain, piecewise

    expr = piecewise([(0, 100), (10, 300)])
    assert expr.startswith("if(lt(t,0.000)") and "300.00" in expr
    assert linear_gain({"input_i": -20.0, "input_tp": -3.0}, -14.0) == 2.0  # limited by true peak (-1 dBTP)
    assert linear_gain({"input_i": -10.0, "input_tp": -0.5}, -14.0) == -4.0
    assert linear_gain({"input_i": -70.0}, -14.0) is None  # silence: no gain


def test_render_executes_action_plan_with_zoom_fades_and_loudness(tmp_path):
    from clipper import media
    from clipper.render.renderer import RenderSpec, render

    plan = EditPlan(layout="action", crop_keyframes=[{"t": 0, "cx": 0.3}, {"t": 8, "cx": 0.7}],
                    zooms=[{"t": 4.0, "duration": 1.4, "scale": 1.15}], caption_style="boxed", normalize_audio=True,
                    title="Teste").model_dump()
    res = asyncio.run(render(RenderSpec(source=FIXTURES / "chase_crash.mp4", source_start=0, start=30, end=40, peak=35.4,
                                        words=[], plan=plan, preset="ultrafast", crf=30), tmp_path))
    info = media.probe_sync(res.video)
    v = media.video_stream(info)
    assert (v["width"], v["height"]) == (1080, 1920)
    assert abs(media.duration_of(info) - 10.0) < 0.5
    assert res.layout == "action" and "zoom:1" in res.applied and "fades" in res.applied
    assert res.audio.get("gain_db") is not None and res.audio["measured_lufs"] is not None


def test_director_ignores_window_edge_and_low_confidence_vlm_arcs():
    """Measured failure mode: the 2B VLM answered setup=first frame / resolution=last frame."""
    lazy = VisualAnalysis(provider="vlm", model="m", window_abs=(10.0, 50.0), unexpected=8, confidence=0.9,
                          events=[VisualEvent(t=20.0, label="x", importance=8)],
                          arc=NarrativeArc(setup_start=0.0, climax=20.0, resolution_end=40.0))
    d = director.decide(_cand(), [], director.AcousticTrack(), lazy, LIMITS)
    assert d.start_abs >= 15.0 and d.end_abs <= 46.0
    assert any("ignorado" in a for a in d.adjustments)
    unsure = lazy.model_copy(update={"confidence": 0.3, "arc": NarrativeArc(setup_start=5.0, climax=20.0)})
    d2 = director.decide(_cand(), [], director.AcousticTrack(), unsure, LIMITS)
    assert d2.start_abs >= 15.0
