"""Phase 6A: Quality Gate 2.0 - four dimensions, five outcomes, bounded REWORK."""

from __future__ import annotations

from sqlalchemy import select

from clipper.ai import quality_gate as qg
from clipper.ai.editorial import LABELS
from clipper.db import session_scope
from clipper.models import Clip, Job, Segment, Streamer, StreamSession, TranscriptWord
from clipper import pipeline_ai, storage_lifecycle


def J(story=None, final=60.0, waiting=False, **vals):
    base = {"interest": 7, "humor": None, "surprise": 6, "tension": None, "reaction": 7, "originality": 6,
            "resolution": None, "context": 7, "narrative": 7, "standalone": 7, "retention": 7, "audio": 7,
            "visual": None, "redundancy": 10}
    base.update(vals)
    return {"criteria": {k: {"label": LABELS[k], "value": v, "evidence": []} for k, v in base.items()},
            "story": story, "final": final, "waiting_context": waiting}


TALK = [(t, t + 0.4) for t in [x * 0.8 for x in range(1, 40)]]  # continuous speech over 0-32 s


def ev(j, *, start=0.0, end=30.0, peak=12.0, keep=True, words=TALK, media=(-300.0, 300.0), kinds=None, **kw):
    return qg.evaluate(judgement=j, start=start, end=end, peak=peak, keep=keep, words=words, media=media,
                       kinds=kinds or ["keyword"], **kw)


def test_four_separate_explained_dimensions_and_ready():
    r = ev(J())
    assert r.outcome == qg.READY
    for d in (r.event, r.narrative, r.recovery, r.technical):
        assert d.score is not None and d.evidence
    assert r.as_dict()["outcome_label"] == "Pronto para revisão"


def test_good_event_with_bad_opening_is_reworked_from_the_situation_start():
    story = {"start": -40.0, "summary": "perseguição", "kind": "police_chase"}
    r = ev(J(story=story, context=2, narrative=3, standalone=3))
    assert r.outcome == qg.REWORK
    fix = next(f for f in r.fixes if f.kind == "context_before")
    assert fix.applicable and fix.start == -41.0


def test_climax_after_the_original_limit_extends_the_end():
    story = {"start": 0.0, "climax_t": 45.0, "summary": "fuga", "kind": "police_chase"}
    r = ev(J(story=story, narrative=3, retention=3))
    assert r.outcome == qg.REWORK
    assert next(f for f in r.fixes if f.kind == "outcome_after").end == 49.0


def test_outcome_outside_the_available_media_cannot_be_fixed():
    story = {"start": 0.0, "climax_t": 45.0, "summary": "fuga", "kind": "police_chase"}
    r = ev(J(story=story, narrative=3, retention=3), media=(0.0, 31.0))
    assert not any(f.applicable for f in r.fixes)
    assert r.outcome == qg.HUMAN_REVIEW  # good happening, the edit cannot fix it alone


def test_late_start_moves_toward_the_peak():
    r = ev(J(retention=2, narrative=3, context=4), end=40.0, peak=33.0)
    f = next(f for f in r.fixes if f.kind == "late_peak")
    assert r.outcome == qg.REWORK and f.start > 0


def test_chase_without_outcome_waits_for_context():
    r = ev(J(story={"start": 0, "status": "ongoing", "summary": "perseguição"}, waiting=True))
    assert r.outcome == qg.CONTEXT_PENDING


def test_funny_conversation_without_visual_changes_is_not_penalised():
    r = ev(J(humor=8, visual=None), kinds=["laughter", "keyword"])
    assert r.outcome == qg.READY


def test_lots_of_movement_without_narrative_interest_is_rejected():
    r = ev(J(interest=3, surprise=2, reaction=2, originality=3, visual=9), keep=False, visual_ok=True,
           kinds=["game_impact"])
    assert r.outcome == qg.REJECTED and "acontecimento fraco" in r.reasons[0]


def test_dead_time_lowers_the_narrative_and_is_reported_for_the_editor():
    sparse = [(0.5, 1.0), (14.0, 14.5), (28.0, 28.5)]
    r = ev(J(), words=sparse)
    assert any("sem fala" in e for e in r.narrative.evidence)
    dead = next(f for f in r.fixes if f.kind == "dead_time")
    assert not dead.applicable  # cutting pauses needs the structural editor (6B)


def test_rework_is_bounded_and_stops_when_it_does_not_improve():
    story = {"start": -40.0, "summary": "x"}
    j = J(story=story, context=2, narrative=3, standalone=3)
    assert ev(j, rework_runs=1, history=[{"narrative": 1.0}]).outcome == qg.REWORK  # improved by > 0.5
    stalled = ev(j, rework_runs=1, history=[{"narrative": 4.0}])
    assert stalled.outcome == qg.HUMAN_REVIEW and any("sem melhora" in x for x in stalled.reasons)
    assert ev(j, rework_runs=2, history=[{"narrative": 1.0}]).outcome == qg.HUMAN_REVIEW  # max reached


def test_manual_approval_and_test_clips_are_never_discarded():
    weak = J(interest=1, surprise=1, reaction=1, originality=1)
    assert ev(weak, keep=False, approved=True).outcome == qg.READY
    assert ev(weak, keep=False, is_test=True).outcome == qg.READY


def test_conflicting_editorial_models_go_to_a_human():
    assert ev(J(), keep=False).conflict  # gate likes it, director refused
    r = ev(J(interest=2, surprise=2, reaction=2, originality=2, final=80), keep=True)
    assert r.outcome == qg.HUMAN_REVIEW and r.conflict


def test_no_editorial_evidence_follows_the_director():
    empty = J(interest=None, surprise=None, reaction=None, originality=None)
    assert ev(empty, keep=True).outcome == qg.READY
    assert ev(empty, keep=False).outcome == qg.REJECTED


# ------------------------------------------------------------------ through the pipeline
def _clip(tag: str) -> int:
    with session_scope() as db:
        s = Streamer(platform="twitch", channel=f"qg_{tag}", url=f"https://www.twitch.tv/qg_{tag}")
        db.add(s)
        db.flush()
        sess = StreamSession(streamer_id=s.id, status="ended")
        db.add(sess)
        db.flush()
        for i in range(30):
            db.add(Segment(session_id=sess.id, idx=i, start=i * 10.0, duration=10.0, analysis_state="done"))
        for k in range(40):
            t = 100 + k * 0.8
            db.add(TranscriptWord(session_id=sess.id, segment_idx=int(t // 10), start=t, end=t + 0.4, word="fala"))
        c = Clip(session_id=sess.id, streamer_id=s.id, start=100, end=130, peak=112, score=60, status="analyzing",
                 kinds=["keyword"], analysis_runs=1, version=1)
        db.add(c)
        db.flush()
        return c.id


def _gate(cid, judgement, keep=True, bounds=(100.0, 130.0)):
    from clipper.ai.schemas import DirectorDecision
    from clipper.settings_store import load_ai_settings

    with session_scope() as db:
        c = db.get(Clip, cid)
        c.decision = {"editorial": judgement}
        d = DirectorDecision(start_abs=bounds[0], end_abs=bounds[1], peak_abs=112, score=60, base_score=60, keep=keep,
                             mode="audio_only")
        held = pipeline_ai._apply_gate(db, c, d, None, load_ai_settings(db))
        return held, c.status, c.gate, dict(c.provenance or {}).get("gate")


def test_pipeline_rework_requeues_with_new_bounds_and_spec_uses_them(client):
    cid = _clip("rw")
    j = J(story={"start": 60.0, "summary": "perseguição"}, context=2, narrative=3, standalone=3)
    held, status, gate, g = _gate(cid, j)
    assert held and status == "analyzing" and gate == "REWORK"
    assert g["pending"]["start"] == 59.0 and g["rework_runs"] == 1
    with session_scope() as db:
        assert db.scalar(select(Job).where(Job.type == "vision_analyze", Job.payload["clip_id"].as_integer() == cid))
        c = db.get(Clip, cid)
        spec = pipeline_ai.ai_spec(db, c, {"source": "api"})
        assert spec["start"] == 59.0
        # a clip in rework is "in processing": the storage lifecycle never touches it
        assert c.status in storage_lifecycle.BUSY


def test_pipeline_rejection_and_human_review(client):
    cid = _clip("rej")
    held, status, gate, _ = _gate(cid, J(interest=1, surprise=1, reaction=1, originality=1, final=30), keep=False)
    assert held and status == "rejected" and gate == "REJECTED"
    cid2 = _clip("hr")
    held, status, gate, _ = _gate(cid2, J(), keep=False)
    assert not held and gate == "HUMAN_REVIEW"  # rendered so a person can decide
