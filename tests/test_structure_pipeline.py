"""Phase 6B in the pipeline: variants never touch the main video, comparisons are evidence."""

from __future__ import annotations

from sqlalchemy import select

from clipper import pipeline, pipeline_structure as ps
from clipper.db import session_scope
from clipper.models import AudioFeature, Clip, EditComparison, Job, Segment, Streamer, StreamSession, TranscriptWord
from clipper.settings_store import load_ai_settings
from conftest import CSRF


def _clip(tag: str, *, approved=False) -> int:
    """A rendered 40 s clip with long pauses, a peak at 130 and laughter 130-133."""
    with session_scope() as db:
        s = Streamer(platform="twitch", channel=f"sp_{tag}", url=f"https://www.twitch.tv/sp_{tag}")
        db.add(s)
        db.flush()
        sess = StreamSession(streamer_id=s.id, status="ended", segment_base_url="http://127.0.0.1:9/x")
        db.add(sess)
        db.flush()
        for i in range(20):
            db.add(Segment(session_id=sess.id, idx=i, start=i * 10.0, duration=10.0, analysis_state="done"))
            cats = {"Speech": [0.9] * 21, "Laughter": [0.0] * 21}
            if i == 13:
                cats["Laughter"] = [0.9] * 7 + [0.0] * 14  # 130-133.4
            db.add(AudioFeature(session_id=sess.id, segment_idx=i, start=i * 10.0, hop=0.25, loudness_db=[], centroid_hz=[],
                                flux=[], extra={"yamnet": {"hop": 0.48, "cats": cats}}))
        for t in (101, 101.6, 106, 106.5, 114, 114.5, 122, 122.5, 127, 127.5, 128.1, 133.5, 134.1):
            db.add(TranscriptWord(session_id=sess.id, segment_idx=int(t // 10), start=t, end=t + 0.4, word="fala",
                                  speaker="streamer"))
        c = Clip(session_id=sess.id, streamer_id=s.id, start=100, end=140, peak=130, score=70, kinds=["laughter"],
                 status="approved" if approved else "ready", video_path=f"clips/{tag}/clip_v1.mp4",
                 thumb_path=f"clips/{tag}/thumb_v1.jpg", version=1, source_path=f"clips/{tag}/source.mp4",
                 source_start=95.0, source_duration=50.0)
        db.add(c)
        db.flush()
        return c.id


def test_shadow_variant_is_queued_without_touching_the_main_video(client):
    cid = _clip("shadow")
    with session_scope() as db:
        c = db.get(Clip, cid)
        s = load_ai_settings(db)
        s.editor_variants_per_hour = 120  # independent of the jobs other tests created this hour
        out = ps.plan_variant(db, c, s)
        assert out["queued"] and out["structure"] == "C"  # laughter -> humor of reaction
        assert c.video_path.endswith("clip_v1.mp4") and c.status == "ready" and c.start == 100
        job = db.scalar(select(Job).where(Job.type == "render", Job.payload["clip_id"].as_integer() == cid))
        assert job.payload["variant"] == "edit" and job.priority == ps.VARIANT_PRIORITY
        spec = pipeline.render_spec(db, cid, "edit")
        assert spec["store_source"] is False and spec["variant"] == "edit"
        assert spec["edit_plan"]["segments"] and spec["start"] >= 100 and spec["end"] - spec["start"] < 40
        assert out["metrics"]["reactions_kept"] and out["metrics"]["chronological"]
        assert out["metrics"]["duration"] < out["original_metrics"]["duration"]


def test_original_kept_when_no_meaningful_change_and_budget_respected(client):
    cid = _clip("budget")
    with session_scope() as db:
        s = load_ai_settings(db)
        s.editor_variants_per_hour = 0
        c = db.get(Clip, cid)
        assert ps.plan_variant(db, c, s)["queued"] is False  # automatic: over budget
        assert ps.plan_variant(db, c, s, manual=True)["queued"] is True  # a person asked for it


def test_failed_variant_render_never_fails_the_clip(client):
    cid = _clip("dead")
    with session_scope() as db:
        c = db.get(Clip, cid)
        ps.plan_variant(db, c, load_ai_settings(db), manual=True)
        job = db.scalar(select(Job).where(Job.type == "render", Job.payload["clip_id"].as_integer() == cid))
        job.last_error = "ffmpeg killed"
        pipeline.on_job_dead(db, job)  # interrupted render ends in the dead-letter queue
        c = db.get(Clip, cid)
        assert c.status == "ready" and c.video_path.endswith("clip_v1.mp4")
        assert c.provenance["variants"]["edit"]["state"] == "failed"


def test_promote_swaps_main_and_variant_and_comparison_is_recorded(client, admin):
    cid = _clip("promo")
    with session_scope() as db:
        c = db.get(Clip, cid)
        ps.plan_variant(db, c, load_ai_settings(db), manual=True)
        ps.store_variant(db, c, "edit", "clips/promo/clip_v1_edit.mp4", "", 1234)
    r = admin.post(f"/api/clips/{cid}/compare", json={"left": "main", "right": "edit", "winner": "right"}, headers=CSRF)
    assert r.status_code == 200 and r.json()["right_structure"] == "C" and r.json()["left_structure"] == "O"
    r = admin.post(f"/api/clips/{cid}/variants/edit/promote", headers=CSRF)
    assert r.status_code == 200, r.text
    with session_scope() as db:
        c = db.get(Clip, cid)
        assert c.video_path.endswith("clip_v1_edit.mp4") and c.edit_plan["structure"] == "C"
        assert c.provenance["variants"]["original"]["video_path"].endswith("clip_v1.mp4")  # original kept
        assert db.query(EditComparison).filter(EditComparison.clip_id == cid).count() == 1
    res = admin.get("/api/clips/edit-comparisons/results").json()
    assert res["by_structure"]["C"]["won"] >= 1
    ov = admin.get(f"/api/clips/{cid}/variants").json()
    assert ov["main"]["structure"] == "C" and "original" in ov["variants"]


def test_restructure_on_demand_and_unrendered_variant_cannot_be_promoted(admin):
    cid = _clip("demand")
    r = admin.post(f"/api/clips/{cid}/restructure", json={"structure": "A"}, headers=CSRF)
    assert r.status_code == 200 and r.json()["structure"] == "A"
    assert admin.post(f"/api/clips/{cid}/variants/edit/promote", headers=CSRF).status_code == 409
    opts = admin.get(f"/api/clips/{cid}/structures").json()
    assert {o["structure"] for o in opts} == {"A", "B", "C", "D", "E"}
    assert any(not o["applicable"] and o["why_not"] for o in opts)  # B: no long situation, explained


def test_approved_clip_keeps_its_main_video_after_a_variant(client):
    cid = _clip("appr", approved=True)
    with session_scope() as db:
        c = db.get(Clip, cid)
        ps.plan_variant(db, c, load_ai_settings(db), manual=True)
        assert c.status == "approved" and c.video_path.endswith("clip_v1.mp4")


def test_stale_variant_upload_is_discarded(client):
    cid = _clip("stale")
    with session_scope() as db:
        c = db.get(Clip, cid)
        drop = ps.store_variant(db, c, "edit", "clips/stale/clip_v1_edit.mp4", "", 10)
        assert drop == ["clips/stale/clip_v1_edit.mp4"] and "variants" not in (c.provenance or {})
