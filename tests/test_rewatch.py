"""Phase 5 stage B: deep second analysis (re-watch) and post-live retrospective."""

from __future__ import annotations

from datetime import timedelta

from sqlalchemy import select

from clipper import pipeline_ai, pipeline_story, queue
from clipper.db import session_scope, utcnow
from clipper.models import Candidate, Clip, Job, Segment, Streamer, StreamSession, TranscriptWord, AudioFeature
from clipper.settings_store import AISettings, load_ai_settings


def _session(tag, status="live"):
    with session_scope() as db:
        s = Streamer(platform="twitch", channel=f"rw_{tag}", url=f"https://www.twitch.tv/rw_{tag}", display_name=tag)
        db.add(s)
        db.flush()
        sess = StreamSession(streamer_id=s.id, status=status, covered_until=400, analyzed_until=400, scored_until=400)
        db.add(sess)
        db.flush()
        for i in range(40):
            db.add(Segment(session_id=sess.id, idx=i, start=i * 10.0, duration=10.0, analysis_state="done"))
        return sess.id, s.id


def _payload(score, start=100.0, end=130.0, fallback=False):
    return {"decision": {"start_abs": start, "end_abs": end, "peak_abs": 115.0, "score": score, "base_score": score,
                         "keep": score >= 55, "mode": "multimodal" if not fallback else "audio_only"},
            "edit_plan": {"title": "t"},
            "visual": {"provider": "openvino-vlm:qwen3-vl-2b-int4@GPU", "summary": "carro bate na fuga", "funny": 3,
                       "important": 7, "confidence": 0.8, "fallback": fallback},
            "meta": {"vision_provider": "openvino-vlm:qwen3-vl-2b-int4@GPU"}}


def _clip(sid, streamer_id, **kw):
    with session_scope() as db:
        c = Clip(session_id=sid, streamer_id=streamer_id, start=100, end=130, peak=115, score=60, status="analyzing",
                 vision_state="pending", kinds=["scream"], score_breakdown={"audience": 6, "reaction": 6}, **kw)
        db.add(c)
        db.flush()
        return c.id


def test_uncertain_clip_is_rewatched_before_rendering_and_can_improve(client):
    sid, st = _session("deep")
    cid = _clip(sid, st)
    with session_scope() as db:
        clip = db.get(Clip, cid)
        pipeline_ai.apply_analysis(db, clip, _payload(52.0))  # near the minimum (55): uncertain
        assert clip.provenance["review"]["pending"] == "deep" and clip.status == "analyzing"
        assert not db.scalar(select(Job).where(Job.type == "render", Job.payload["clip_id"].as_integer() == cid)) \
            if db.get_bind().dialect.name == "postgresql" else True
        spec = pipeline_ai.ai_spec(db, clip, {"source": "segments"})
        assert spec["deep"] and spec["max_frames"] == 12
    with session_scope() as db:
        clip = db.get(Clip, cid)
        pipeline_ai.apply_analysis(db, clip, _payload(68.0, start=95.0, end=135.0))  # the re-watch result
        rv = clip.provenance["review"]
        assert rv["done"] == "deep" and rv["first_score"] == 52.0 and rv["score"] == 68.0
        assert rv["bounds_change_s"] == [-5.0, 5.0] and clip.status == "rendering"
        assert any("Revisão aprofundada" in r for r in clip.reasons)


def test_confident_clip_is_not_rewatched_and_fallback_never_triggers_it(client):
    sid, st = _session("nodeep")
    a, b = _clip(sid, st), _clip(sid, st)
    with session_scope() as db:
        clip = db.get(Clip, a)
        pipeline_ai.apply_analysis(db, clip, _payload(90.0))
        assert "review" not in (clip.provenance or {}) and clip.status == "rendering"
        clip_b = db.get(Clip, b)
        pipeline_ai.apply_analysis(db, clip_b, _payload(52.0, fallback=True))
        assert "review" not in (clip_b.provenance or {})


def test_rewatch_is_skipped_under_load(client):
    sid, st = _session("load")
    cid = _clip(sid, st)
    with session_scope() as db:
        s = load_ai_settings(db)
        for i in range(s.vision_queue_max):
            queue.enqueue(db, "vision_analyze", {"clip_id": -1000 - i}, idempotency_key=f"loadtest:{sid}:{i}")
        clip = db.get(Clip, cid)
        pipeline_ai.apply_analysis(db, clip, _payload(52.0))
        assert "review" not in (clip.provenance or {})
        for j in db.scalars(select(Job).where(Job.idempotency_key.like(f"loadtest:{sid}:%"))):
            j.status = "cancelled"


def test_post_live_retrospective_recovers_stories_and_near_misses(client):
    sid, st = _session("retro", status="ended")
    with session_scope() as db:
        sess = db.get(StreamSession, sid)
        sess.ended_at = utcnow() - timedelta(minutes=5)
        for t, text in ((100, "a policia ta vindo atras da gente acelera"), (110, "corre a viatura ta colada"),
                        (140, "nao acredito bateu bateu"), (160, "despistei kkkk kkkk")):
            for i, w in enumerate(text.split()):
                db.add(TranscriptWord(session_id=sid, segment_idx=t // 10, start=t + i * 0.4, end=t + i * 0.4 + .3, word=w))
        for t, cats in ((100, {"vehicle": 0.6}), (110, {"vehicle": 0.7}), (140, {"game_impact": 0.7, "scream": 0.6}),
                        (160, {"laughter": 0.6})):
            db.add(AudioFeature(session_id=sid, segment_idx=t // 10, start=t, hop=0.25, loudness_db=[], centroid_hz=[],
                                flux=[], extra={"yamnet": {"hop": 0.48, "cats": {k: [v] * 20 for k, v in cats.items()}}}))
        db.add(Candidate(session_id=sid, streamer_id=st, peak=300, start=290, end=320, score=50, decision="below_min_score"))
        db.add(Candidate(session_id=sid, streamer_id=st, peak=350, start=340, end=370, score=20, decision="below_min_score"))
    with session_scope() as db:
        made = pipeline_story.run_retrospectives(db, AISettings())
        assert made >= 2
        clips = db.scalars(select(Clip).where(Clip.session_id == sid)).all()
        assert any(c.situation_id for c in clips)  # the chase story
        assert any(abs(c.peak - 300) < 1 for c in clips)  # the near miss (50 >= 55 - 8)
        assert not any(abs(c.peak - 350) < 1 for c in clips)  # a weak one is not resurrected
        assert all((c.provenance or {}).get("origin") == "retrospective" for c in clips)
    with session_scope() as db:
        assert pipeline_story.run_retrospectives(db, AISettings()) == 0  # once per live
