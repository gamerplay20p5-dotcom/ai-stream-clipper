"""Phase 5 stage E: who is talking - turns, labels, streamer voice hypothesis and confirmation."""

from __future__ import annotations

import json

import numpy as np
import pytest
from sqlalchemy import select

from clipper.ai import speaker as spk
from clipper.db import session_scope
from clipper.models import Segment, SpeakerProfile, SpeechTurn, Streamer, StreamSession, TranscriptWord
from clipper.settings_store import load_ai_settings, save_ai_settings
from conftest import CSRF, sign_headers

RNG = np.random.default_rng(7)


def _unit(v):
    return v / np.linalg.norm(v)


A = _unit(RNG.normal(size=256))  # the streamer
B = _unit(RNG.normal(size=256))  # a friend on the radio
B = _unit(B - (B @ A) * A)  # orthogonal to A
C = _unit(RNG.normal(size=256))
C = _unit(C - (C @ A) * A - (C @ B) * B)


def _voice(base, noise=0.25):
    return [round(float(x), 4) for x in _unit(base + noise * _unit(RNG.normal(size=256)) * 0.5)]


def _ambiguous():  # cosine ~0.45 to the streamer: between the two thresholds
    return [round(float(x), 4) for x in _unit(0.45 * A + np.sqrt(1 - 0.45 ** 2) * C)]


# ------------------------------------------------------------------ pure parts
def test_words_are_grouped_into_turns_at_pauses_and_length():
    words = [[0.0, 0.3, "oi"], [0.4, 0.8, "galera"], [2.0, 2.4, "bora"], [2.5, 2.9, "lá"]]
    assert [t[2] for t in spk.word_turns(words)] == ["oi galera", "bora lá"]
    long = [[i * 0.5, i * 0.5 + 0.4, f"w{i}"] for i in range(40)]  # 20 s without pauses
    assert all(b - a <= spk.MAX_TURN_S for a, b, _ in spk.word_turns(long))


def test_uncertain_voice_is_unknown_never_forced():
    label, conf, _ = spk.assign(_ambiguous(), list(A), [])
    assert label == "unknown"
    assert spk.assign(_voice(A), list(A), [])[0] == "streamer"
    label, _c, clusters = spk.assign(_voice(B), list(A), [])
    assert label == "other:1" and len(clusters) == 1
    assert spk.classify(_voice(B), list(A), clusters)[0] == "other:1"


# ------------------------------------------------------------------ through the internal API
def _session(tag: str, n: int = 40) -> tuple[int, int]:
    with session_scope() as db:
        s = Streamer(platform="twitch", channel=f"spk_{tag}", url=f"https://www.twitch.tv/spk_{tag}", display_name=tag)
        db.add(s)
        db.flush()
        sess = StreamSession(streamer_id=s.id, status="live")
        db.add(sess)
        db.flush()
        for i in range(n):
            db.add(Segment(session_id=sess.id, idx=i, start=i * 10.0, duration=10.0, analysis_state="pending"))
        return s.id, sess.id


def _post(client, sid: int, body: dict):
    raw = json.dumps(body).encode()
    path = f"/internal/sessions/{sid}/analysis"
    h = sign_headers("POST", path, raw, worker="audio-test")
    r = client.post(path, content=raw, headers={**h, "Content-Type": "application/json"})
    assert r.status_code == 200, r.text
    return r


def _segment(i: int, extra_turns: list | None = None, with_turns: bool = True) -> dict:
    words = [[1.0, 1.4, f"streamer{i}"], [3.0, 3.4, "fala"], [7.5, 7.9, f"amigo{i}"]]
    body = {"segment_idx": i, "features": {"hop": 0.25}, "words": words, "speech_ratio": 0.8, "language": "pt"}
    if with_turns:
        body["speech_turns"] = [{"start": 0.5, "end": 6.5, "text": f"streamer{i} fala", "emb": [_voice(A)]},
                                {"start": 7.0, "end": 9.5, "text": f"amigo{i}", "emb": [_voice(B)]}] + (extra_turns or [])
    return body


def _labels(sess_id: int) -> dict[str, str]:
    with session_scope() as db:
        return {w.word: w.speaker for w in db.scalars(select(TranscriptWord).where(TranscriptWord.session_id == sess_id))}


def test_streamer_voice_hypothesis_forms_from_speech_time_and_labels_words(client):
    st_id, sid = _session("hyp")
    for i in range(30):
        _post(client, sid, _segment(i))
    with session_scope() as db:
        assert db.scalar(select(SpeakerProfile).where(SpeakerProfile.streamer_id == st_id)) is None  # 255 s: too early
    for i in range(30, 40):
        _post(client, sid, _segment(i))
    with session_scope() as db:
        p = db.scalar(select(SpeakerProfile).where(SpeakerProfile.streamer_id == st_id))
        assert p is not None and p.confirmed is False and p.source == "auto"  # a hypothesis, not a certainty
        labels = {t.label for t in db.scalars(select(SpeechTurn).where(SpeechTurn.session_id == sid))}
        assert labels == {"streamer", "other:1"}
    w = _labels(sid)
    assert w["streamer3"] == "streamer" and w["amigo3"] == "other:1"
    assert w["streamer39"] == "streamer" and w["amigo39"] == "other:1"


def test_ambiguous_and_mixed_turns(client):
    st_id, sid = _session("mix")
    with session_scope() as db:
        db.add(SpeakerProfile(streamer_id=st_id, centroid=list(map(float, A)), n=10, confirmed=True,
                              source="confirmed_cluster", sample={}))
    body = _segment(0)
    body["words"] = [[1.0, 1.4, "eu"], [7.5, 7.9, "ele"]]
    body["speech_turns"] = [{"start": 0.5, "end": 6.5, "text": "eu ... ele", "emb": [_voice(A), _voice(A), _voice(B)]},
                            {"start": 7.0, "end": 9.5, "text": "talvez", "emb": [_ambiguous()]}]
    _post(client, sid, body)
    with session_scope() as db:
        turns = db.scalars(select(SpeechTurn).where(SpeechTurn.session_id == sid).order_by(SpeechTurn.start)).all()
        assert [t.label for t in turns] == ["overlap", "unknown"]
    w = _labels(sid)
    assert w["eu"] == "streamer"  # first half of the mixed turn is the streamer's voice
    assert w["ele"] == "unknown"  # inside the ambiguous turn: never guessed


def test_old_workers_and_disabled_setting_leave_words_unlabelled(client):
    _st, sid = _session("old", 2)
    _post(client, sid, _segment(0, with_turns=False))
    with session_scope() as db:
        s = load_ai_settings(db)
        s.speakers_enabled = False
        save_ai_settings(db, s)
    try:
        _post(client, sid, _segment(1))
    finally:
        with session_scope() as db:
            s = load_ai_settings(db)
            s.speakers_enabled = True
            save_ai_settings(db, s)
    assert set(_labels(sid).values()) == {""}
    with session_scope() as db:
        assert db.query(SpeechTurn).filter(SpeechTurn.session_id == sid).count() == 0


def test_owner_rejects_wrong_hypothesis_and_confirms_the_right_voice(client, admin):
    st_id, sid = _session("own")
    # the friend talks MORE than the streamer here: the automatic guess will be wrong
    for i in range(40):
        body = _segment(i)
        body["speech_turns"] = [{"start": 0.5, "end": 3.0, "text": f"streamer{i}", "emb": [_voice(A)]},
                                {"start": 3.5, "end": 9.5, "text": f"amigo{i}", "emb": [_voice(B)]}]
        body["words"] = [[1.0, 1.4, f"streamer{i}"], [7.5, 7.9, f"amigo{i}"]]
        _post(client, sid, body)
    ov = admin.get(f"/api/speakers/streamers/{st_id}").json()
    assert ov["profile"] and ov["profile"]["confirmed"] is False
    assert ov["groups"][0]["label"] == "streamer" and "amigo" in ov["groups"][0]["samples"][0]["text"]
    assert _labels(sid)["amigo5"] == "streamer"  # wrong guess, still only a hypothesis
    other = next(g["label"] for g in ov["groups"] if g["label"].startswith("other:"))

    r = admin.post(f"/api/speakers/streamers/{st_id}/reject", json={"session_id": sid, "label": "streamer"},
                   headers=CSRF)
    assert r.status_code == 200, r.text
    ov = admin.get(f"/api/speakers/streamers/{st_id}").json()
    assert ov["rejected"] == 1
    other = next(g["label"] for g in ov["groups"] if g["label"].startswith("other:")
                 and "streamer" in g["samples"][0]["text"]) if ov["profile"] is None else "streamer"
    r = admin.post(f"/api/speakers/streamers/{st_id}/confirm", json={"session_id": sid, "label": other},
                   headers=CSRF)
    assert r.status_code == 200, r.text
    ov = admin.get(f"/api/speakers/streamers/{st_id}").json()
    assert ov["profile"]["confirmed"] is True
    w = _labels(sid)
    assert w["streamer5"] == "streamer" and w["amigo5"].startswith("other:")
    assert admin.post(f"/api/speakers/streamers/{st_id}/confirm", json={"session_id": sid, "label": "x"},
                      headers=CSRF).status_code == 422


def test_turn_audio_requires_a_stored_source(admin):
    _st, sid = _session("aud", 1)
    with session_scope() as db:
        t = SpeechTurn(session_id=sid, segment_idx=0, start=1.0, end=3.0, emb=[list(map(float, A))], label="streamer",
                       confidence=0.9, text="oi")
        db.add(t)
        db.flush()
        tid = t.id
    assert admin.get(f"/api/speakers/turns/{tid}/audio").status_code == 404


@pytest.mark.parametrize("bad", [{"session_id": 999999, "label": "streamer"}])
def test_confirm_checks_the_session_belongs_to_the_streamer(admin, bad):
    st_id, _sid = _session("chk", 1)
    assert admin.post(f"/api/speakers/streamers/{st_id}/confirm", json=bad, headers=CSRF).status_code == 404


def test_one_off_sounds_never_lock_out_new_voices():
    """Production v0.5.5: 12 singletons (music, game effects) filled every slot and the streamer
    became 'unknown' for minutes. The weakest one-off group now makes room."""
    clusters: list[dict] = []
    for _ in range(spk.MAX_CLUSTERS):
        _l, _c, clusters = spk.assign([float(x) for x in _unit(RNG.normal(size=256))], None, clusters)
    assert len(clusters) == spk.MAX_CLUSTERS
    labels = []
    for _ in range(5):
        label, _c, clusters = spk.assign(_voice(A), None, clusters)
        labels.append(label)
    assert "unknown" not in labels and len(set(labels)) == 1  # one stable group for the new voice
    assert len(clusters) == spk.MAX_CLUSTERS
    ids = [c["id"] for c in clusters]
    assert len(ids) == len(set(ids)) and max(ids) > spk.MAX_CLUSTERS  # ids never reused
