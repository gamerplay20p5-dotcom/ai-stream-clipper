"""Phase 5 stage A in the real pipeline: DB-fed story memory, association of candidates, waiting state."""

from __future__ import annotations

from sqlalchemy import select

from clipper import pipeline, pipeline_ai
from clipper.db import session_scope
from clipper.models import AudioFeature, Candidate, Clip, SituationRow, Streamer, StreamSession, TranscriptWord
from clipper.pipeline_story import plan_cut, story_context, update_story


def _session(tag):
    with session_scope() as db:
        s = Streamer(platform="twitch", channel=f"st_{tag}", url=f"https://www.twitch.tv/st_{tag}", display_name=tag)
        db.add(s)
        db.flush()
        sess = StreamSession(streamer_id=s.id, status="live", covered_until=400, analyzed_until=400)
        db.add(sess)
        db.flush()
        return sess.id


def _say(db, sid, t, text):
    for i, w in enumerate(text.split()):
        db.add(TranscriptWord(session_id=sid, segment_idx=int(t // 10), start=t + i * 0.4, end=t + i * 0.4 + 0.3, word=w))


def _yam(db, sid, t, **cats):
    n = 20
    db.add(AudioFeature(session_id=sid, segment_idx=int(t // 10), start=t, hop=0.25, loudness_db=[], centroid_hz=[],
                        flux=[], extra={"yamnet": {"hop": 0.48, "cats": {k: [v] * n for k, v in cats.items()}}}))


def _chase(sid):
    with session_scope() as db:
        _say(db, sid, 100, "a policia ta vindo atras da gente acelera")
        _yam(db, sid, 100, vehicle=0.6)
        _say(db, sid, 110, "corre a viatura ta colada")
        _yam(db, sid, 110, vehicle=0.7)
        _say(db, sid, 140, "nao acredito bateu bateu")
        _yam(db, sid, 140, vehicle=0.3, game_impact=0.7, scream=0.6)


def test_story_memory_is_built_from_db_and_is_idempotent(client):
    sid = _session("db")
    _chase(sid)
    with session_scope() as db:
        sess = db.get(StreamSession, sid)
        tr = update_story(db, sess, 160)
        assert any(s.kind == "police_chase" for s in tr.situations)
    with session_scope() as db:
        rows = db.scalars(select(SituationRow).where(SituationRow.session_id == sid)).all()
        assert len(rows) == 1 and rows[0].kind == "police_chase" and "colisão" in rows[0].summary
        sess = db.get(StreamSession, sid)
        update_story(db, sess, 160)  # same window again: nothing duplicated
    with session_scope() as db:
        assert db.query(SituationRow).filter(SituationRow.session_id == sid).count() == 1
        assert db.get(StreamSession, sid).stats["story_until"] == 160


def test_candidates_of_the_same_story_merge_or_split_never_by_time_alone(client):
    sid = _session("assoc")
    _chase(sid)
    with session_scope() as db:
        tr = update_story(db, db.get(StreamSession, sid), 160)
        sit = tr.at(140)
        s = db.get(StreamSession, sid)
        pending = Clip(session_id=sid, streamer_id=s.streamer_id, start=125, end=150, peak=140, score=70,
                       status="analyzing", situation_id=sit.id)
        db.add(pending)
        db.flush()
        action, a, b, other = plan_cut(db, sid, sit, 150, 175, 160, 90)
        assert action == "merge" and other.id == pending.id and (a, b) == (125, 175)
        pending.status = "ready"
        action, a, b, _ = plan_cut(db, sid, sit, 130, 150, 140, 90)
        assert action == "duplicate"
        action, a, b, other = plan_cut(db, sid, sit, 175, 200, 185, 90)
        assert action == "second_cut" and other.id == pending.id
        # a candidate with no situation is isolated even if it is right next to the story
        assert plan_cut(db, sid, None, 151, 170, 160, 90)[0] == "isolated"


def test_first_cut_of_a_story_starts_where_the_situation_starts(client):
    sid = _session("ctx")
    _chase(sid)
    with session_scope() as db:
        tr = update_story(db, db.get(StreamSession, sid), 160)
        sit = tr.at(140)
        action, a, b, _ = plan_cut(db, sid, sit, 130, 150, 140, 90)
        assert action == "story" and a <= sit.start and b == 150


def test_weak_clip_in_an_unfolding_story_waits_instead_of_being_rejected(client):
    sid = _session("wait")
    with session_scope() as db:
        _say(db, sid, 100, "a policia ta vindo atras da gente acelera")
        _yam(db, sid, 100, vehicle=0.6)
        _say(db, sid, 110, "corre corre policia atras")
        _yam(db, sid, 110, vehicle=0.6)
    with session_scope() as db:
        tr = update_story(db, db.get(StreamSession, sid), 120)
        sit = tr.at(110)
        assert sit.status == "ongoing" and not sit.complete
        s = db.get(StreamSession, sid)
        clip = Clip(session_id=sid, streamer_id=s.streamer_id, start=100, end=120, peak=110, score=20,
                    status="analyzing", vision_state="pending", situation_id=sit.id,
                    score_breakdown={"audience": 2, "reaction": 2, "context": 2}, kinds=["loudness_spike"])
        db.add(clip)
        db.flush()
        pipeline_ai.direct_without_vision(db, clip, "teste")
        assert clip.status == "waiting" and "desfecho" in clip.error
        ed = clip.decision["editorial"]
        assert ed["waiting_context"] and ed["stage"] == "final" and ed["story"]["kind"] == "police_chase"
        cid = clip.id
    with session_scope() as db:
        sess = db.get(StreamSession, sid)
        sess.status = "ended"
    with session_scope() as db:
        assert pipeline.revisit_waiting_all(db) >= 1
        assert db.get(Clip, cid).status == "analyzing"  # re-analysed with the complete context


def test_story_context_for_the_vlm_is_bounded(client):
    sid = _session("vlmctx")
    _chase(sid)
    with session_scope() as db:
        tr = update_story(db, db.get(StreamSession, sid), 160)
        s = db.get(StreamSession, sid)
        clip = Clip(session_id=sid, streamer_id=s.streamer_id, start=130, end=155, peak=140, score=70,
                    status="ready", situation_id=tr.at(140).id)
        db.add(clip)
        db.flush()
        ctx = story_context(db, clip)
        assert ctx["situation"]["kind"] == "police_chase" and len(ctx["situation"]["summary"]) <= 400
        from clipper.workers.handlers import _story_text

        txt = _story_text(ctx, 120)
        assert "Perseguição policial" in txt and len(txt) <= 500


def test_director_api_lists_sessions_and_situations(admin):
    sid = _session("api")
    _chase(sid)
    with session_scope() as db:
        update_story(db, db.get(StreamSession, sid), 160)
    rows = admin.get("/api/director/sessions?limit=50").json()
    assert any(r["id"] == sid and r["situations"] == 1 for r in rows)
    d = admin.get(f"/api/director/sessions/{sid}").json()
    sit = d["situations"][0]
    assert sit["kind_label"] == "Perseguição policial" and sit["events"] and "summary" in sit
    assert admin.get("/api/director/sessions/999999").status_code == 404
