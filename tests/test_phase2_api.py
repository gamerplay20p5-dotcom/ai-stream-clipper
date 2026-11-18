"""Phase 2 through the API: fallback director, AI result validation, reanalysis, edit plans."""

from __future__ import annotations

import json

from sqlalchemy import select

from clipper.db import session_scope
from clipper.models import AudioFeature, Clip, Job, Segment, Streamer, StreamSession, TranscriptWord
from conftest import CSRF, sign_headers


def _make_clip(tag: str, *, with_game_noise: bool = False) -> int:
    with session_scope() as db:
        s = Streamer(platform="twitch", channel=f"p2_{tag}", url=f"https://www.twitch.tv/p2_{tag}", enabled=False)
        db.add(s)
        db.flush()
        sess = StreamSession(streamer_id=s.id, status="ended", segment_base_url="http://127.0.0.1:9/none")
        db.add(sess)
        db.flush()
        for i in range(8):
            db.add(Segment(session_id=sess.id, idx=i, start=i * 10.0, duration=10.0, analysis_state="done"))
            cats = {"speech": [0.9] * 21, "game_impact": [0.0] * 21, "laughter": [0.0] * 21}
            if with_game_noise and i == 3:
                cats["game_impact"] = [0.9] * 21
            db.add(AudioFeature(session_id=sess.id, segment_idx=i, start=i * 10.0, hop=0.25,
                                loudness_db=[-30.0] * 40, centroid_hz=[300.0] * 40, flux=[0.1] * 40,
                                extra={"yamnet": {"hop": 0.48, "cats": cats}, "pitch_hz": [0.0] * 40}))
        for t, w in ((20.0, "okay"), (28.0, "vou"), (28.4, "comprar"), (29.0, "comida."), (36.0, "beleza.")):
            db.add(TranscriptWord(session_id=sess.id, segment_idx=int(t // 10), start=t, end=t + 0.3, word=w))
        clip = Clip(session_id=sess.id, streamer_id=s.id, start=20.0, end=42.0, peak=31.0, score=70.0,
                    score_breakdown={"intensity": 8}, reasons=["Pico de volume 15 dB"], kinds=["loudness_spike"],
                    title="Título heurístico", status="analyzing", vision_state="pending", version=1,
                    caption_style="bold", layout="auto")
        db.add(clip)
        db.flush()
        return clip.id


def test_fallback_director_runs_in_api_and_discards_game_noise(admin):
    cid = _make_clip("noise", with_game_noise=True)
    from clipper import pipeline

    with session_scope() as db:
        pipeline.post_vision(db, cid, None, "nenhum worker visual")
    c = admin.get(f"/api/clips/{cid}").json()
    assert c["decision"]["mode"] == "audio_only"
    assert c["status"] == "rejected" and "Descartado" in c["error"]
    assert c["analysis_meta"]["vision_fallback"] is True and c["analysis_meta"]["fallback_reason"]
    assert c["edit_plan"]["source"] == "heuristic"
    assert c["heuristic_bounds"] == [20.0, 42.0]


def test_fallback_director_keeps_and_renders_normal_candidates(admin):
    cid = _make_clip("keep")
    from clipper import pipeline

    with session_scope() as db:
        pipeline.post_vision(db, cid, None, "timeout")
        job = db.scalar(select(Job).where(Job.idempotency_key == f"render:{cid}:v1"))
        assert job is not None
    c = admin.get(f"/api/clips/{cid}").json()
    assert c["status"] == "rendering" and c["decision"]["keep"] and c["vision_state"] == "skipped"
    assert any("indisponível" in r for r in c["decision"]["reasons"])


def _post_analysis(client, cid: int, body: dict):
    raw = json.dumps(body).encode()
    path = f"/internal/clips/{cid}/analysis"
    h = sign_headers("POST", path, raw, worker="ai-test")
    return client.post(path, content=raw, headers={**h, "Content-Type": "application/json"})


def test_ai_result_is_applied_and_invalid_results_fall_back(client, admin):
    cid = _make_clip("ai")
    decision = {"start_abs": 12.0, "end_abs": 44.0, "peak_abs": 31.0, "score": 82.0, "base_score": 70.0, "keep": True,
                "mode": "multimodal", "reasons": ["IA visual: batida"], "adjustments": ["Início antecipado"],
                "evidence": [{"source": "visual", "t_abs": 31.0, "detail": "batida"}]}
    plan = {"layout": "action", "crop_keyframes": [{"t": 0, "cx": 0.4}], "base_start_abs": 12.0, "source": "ai"}
    narrative = {"provider": "openvino-llm:qwen3-1.7b-int4@CPU", "titles": ["A batida que ninguém esperava"],
                 "justification": "Perseguição termina em batida.", "fallback": False}
    visual = {"provider": "openvino-vlm:qwen3-vl-2b-int4@CPU", "model": "qwen3-vl-2b-int4", "device": "CPU",
              "summary": "batida", "window_abs": [10.0, 50.0], "unexpected": 8, "confidence": 0.8}
    r = _post_analysis(client, cid, {"decision": decision, "edit_plan": plan, "visual": visual, "narrative": narrative,
                                     "meta": {"vision_model": "qwen3-vl-2b-int4", "timings": {"vlm_s": 41.0}}})
    assert r.status_code == 200 and r.json()["ok"], r.text
    c = admin.get(f"/api/clips/{cid}").json()
    assert (c["start"], c["end"], c["score"]) == (12.0, 44.0, 82.0)
    assert c["title"] == "A batida que ninguém esperava" and "Perseguição" in c["description"]
    assert c["analysis_meta"]["vision_fallback"] is False and c["analysis_meta"]["timings"]["vlm_s"] == 41.0
    assert c["status"] == "rendering" and c["analysis_runs"] == 1
    # a late duplicate is ignored
    assert _post_analysis(client, cid, {"decision": decision, "edit_plan": plan}).json()["stale"] is True

    bad = _make_clip("bad")
    r = _post_analysis(client, bad, {"decision": {**decision, "start_abs": 50.0, "end_abs": 10.0}, "edit_plan": plan})
    assert r.status_code == 200
    c = admin.get(f"/api/clips/{bad}").json()
    assert c["decision"]["mode"] == "audio_only" and "inválido" in c["analysis_meta"]["fallback_reason"]


def test_effective_plan_applies_user_edits_without_reanalysis(admin):
    cid = _make_clip("edit")
    from clipper import pipeline

    with session_scope() as db:
        pipeline.post_vision(db, cid, None, "timeout")
        clip = db.get(Clip, cid)
        clip.edit_plan = {**clip.edit_plan, "layout": "action", "base_start_abs": clip.start,
                          "zooms": [{"t": 9.0, "duration": 1.4, "scale": 1.1}],
                          "crop_keyframes": [{"t": 0, "cx": 0.3}, {"t": 10, "cx": 0.7}]}
        clip.status = "ready"
        start = clip.start
    r = admin.patch(f"/api/clips/{cid}", json={"start": start + 2.0, "caption_style": "clean"}, headers=CSRF)
    assert r.status_code == 200
    with session_scope() as db:
        from clipper import pipeline as p

        spec = p.render_spec(db, cid)
    plan = spec["edit_plan"]
    assert plan["caption_style"] == "clean" and plan["base_start_abs"] == start + 2.0
    assert plan["zooms"][0]["t"] == 7.0  # shifted with the new start, analysis untouched
    assert [k["t"] for k in plan["crop_keyframes"]] == [8.0]
    runs_before = admin.get(f"/api/clips/{cid}").json()["analysis_runs"]
    assert admin.post(f"/api/clips/{cid}/rerender", headers=CSRF).json()["analysis_runs"] == runs_before


def test_manual_reanalysis_enqueues_a_new_analysis_job(admin):
    cid = _make_clip("re")
    from clipper import pipeline

    with session_scope() as db:
        pipeline.post_vision(db, cid, None, "timeout")
        db.get(Clip, cid).status = "ready"
    r = admin.post(f"/api/clips/{cid}/reanalyze", headers=CSRF)
    assert r.status_code == 200 and r.json()["status"] == "analyzing"
    with session_scope() as db:
        assert db.scalar(select(Job).where(Job.idempotency_key == f"vision:{cid}:r1")) is not None
    assert admin.post(f"/api/clips/{cid}/reanalyze", headers=CSRF).status_code == 409
    models = admin.get("/api/ai/models").json()
    assert {m["key"] for m in models} >= {"qwen3-vl-2b-int4", "qwen3-1.7b-int4", "yamnet"}


def test_concurrent_scorers_cannot_create_duplicate_clips(client):
    """Race found in a real run: two score jobs read the same watermark and both inserted."""
    with session_scope() as db:
        s = Streamer(platform="twitch", channel="p2_race", url="https://www.twitch.tv/p2_race")
        db.add(s)
        db.flush()
        sess = StreamSession(streamer_id=s.id, status="live", scored_until=10.0)
        db.add(sess)
        db.flush()
        sid = sess.id
    cand = {"start": 20.0, "end": 40.0, "peak": 30.0, "score": 80.0, "breakdown": {}, "reasons": ["x"], "kinds": [],
            "transcript": "", "title": "t"}
    body = {"from_scored_until": 10.0, "result": {"scored_until": 50.0, "events": [], "candidates": [cand]}}
    path = f"/internal/sessions/{sid}/candidates"
    raw = json.dumps(body).encode()
    first = client.post(path, content=raw, headers={**sign_headers("POST", path, raw), "Content-Type": "application/json"})
    second = client.post(path, content=raw, headers={**sign_headers("POST", path, raw), "Content-Type": "application/json"})
    assert first.status_code == 200 and len(first.json()["clip_ids"]) == 1
    assert second.status_code == 409
    with session_scope() as db:
        assert len(db.scalars(select(Clip).where(Clip.session_id == sid)).all()) == 1
