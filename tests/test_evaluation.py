"""Phase 3: candidates, dataset examples, human reviews, metrics, export, ranker, retention."""

from __future__ import annotations

import json
from datetime import timedelta

import pytest
from sqlalchemy import select

from clipper import pipeline
from clipper.db import session_scope, utcnow
from clipper.evaluation import dataset, metrics, ranker, sampler
from clipper.evaluation.media import enforce_retention
from clipper.models import Candidate, Clip, EvalExample, Job, Review, Segment, Streamer, StreamSession
from clipper.scoring.engine import SCORING_VERSION, score_timeline
from clipper.settings_store import AISettings
from conftest import CSRF, FIXTURES


def _session(tag: str) -> tuple[int, int]:
    with session_scope() as db:
        s = Streamer(platform="twitch", channel=f"ev_{tag}", url=f"https://www.twitch.tv/ev_{tag}",
                     display_name=f"Eval {tag}", status="live")
        db.add(s)
        db.flush()
        sess = StreamSession(streamer_id=s.id, status="live", covered_until=3600.0, analyzed_until=3600.0,
                             segment_base_url="http://capture.test:8781")
        db.add(sess)
        db.flush()
        for i in range(360):
            db.add(Segment(session_id=sess.id, idx=i, start=i * 10.0, duration=10.0, analysis_state="done"))
        return s.id, sess.id


def _feat(**kw):
    base = {"dim_intensity": 5.0, "dim_reaction": 4.0, "n_laughter": 0, "n_scream": 0, "score": 50.0,
            "game_only": 0, "uncorroborated": 0}
    base.update(kw)
    return base


def test_engine_keeps_features_for_accepted_and_rejected():
    tl = json.loads((FIXTURES / "timeline_event.json").read_text())
    res = score_timeline(tl)
    assert res["scoring_version"] == SCORING_VERSION
    assert res["candidates"] and res["candidates"][0]["features"]["n_events"] >= 1
    f = res["candidates"][0]["features"]
    for k in ("delta_db", "dim_intensity", "pre_words", "duration_s", "score"):
        assert k in f
    tl["settings"]["min_score"] = 99.0
    rej = score_timeline(tl)["rejected"]
    assert any(r.get("detail", {}).get("features") for r in rej)


def test_post_candidates_persists_everything_and_samples_for_review(client):
    _, sess_id = _session("persist")
    tl = json.loads((FIXTURES / "timeline_event.json").read_text())
    res = score_timeline(tl)
    # add rejected moments of several kinds
    res["rejected"] += [
        {"peak": 300.0, "reason": "below_min_score", "score": 50.0, "kinds": ["laughter"],
         "detail": {"start": 290.0, "end": 315.0, "score": 50.0, "breakdown": {}, "features": _feat(n_laughter=1),
                    "transcript": "kkkk"}},
        {"peak": 900.0, "reason": "text_only", "score": None, "kinds": ["speech_burst"],
         "detail": {"start": 890.0, "end": 910.0, "score": 20.0, "breakdown": {}, "features": _feat(), "transcript": "x"}},
    ]
    with session_scope() as db:
        ids = pipeline.post_candidates(db, sess_id, 0.0, res)
    with session_scope() as db:
        cands = db.scalars(select(Candidate).where(Candidate.session_id == sess_id)).all()
        decisions = {c.decision for c in cands}
        assert "accepted" in decisions and "below_min_score" in decisions and "text_only" in decisions
        acc = next(c for c in cands if c.decision == "accepted")
        assert acc.clip_id == ids[0] and acc.features and acc.scoring_version == SCORING_VERSION
        clip = db.get(Clip, ids[0])
        assert clip.provenance["scoring"] == SCORING_VERSION and clip.provenance["settings_hash"]
        exs = db.scalars(select(EvalExample).where(EvalExample.session_id == sess_id)).all()
        strata = {e.stratum for e in exs}
        assert "accepted" in strata and "near_threshold" in strata  # 50 is within 12 of the default 55
        assert all(e.split in ("train", "val") for e in exs)
        jobs = db.scalars(select(Job).where(Job.type == "dataset_media")).all()
        assert {j.payload["example_id"] for j in jobs} >= {e.id for e in exs}
        sess = db.get(StreamSession, sess_id)
        assert all("detail" not in r for r in sess.stats["recent_rejected"])  # stats stay light


def test_dataset_failure_never_blocks_clips(client, monkeypatch):
    _, sess_id = _session("safe")
    from clipper.evaluation import dataset as ds

    def boom(*a, **kw):
        raise RuntimeError("dataset down")

    monkeypatch.setattr(ds, "create_example", boom)
    res = score_timeline(json.loads((FIXTURES / "timeline_event.json").read_text()))
    with session_scope() as db:
        ids = pipeline.post_candidates(db, sess_id, 0.0, res)
    assert ids
    with session_scope() as db:
        assert db.get(Clip, ids[0]) is not None


def test_sampler_strata_and_quota_are_deterministic(client):
    _, sess_id = _session("sampler")
    with session_scope() as db:
        s = db.get(StreamSession, sess_id)
        rows = [Candidate(session_id=sess_id, streamer_id=s.streamer_id, peak=100.0 + i, start=90.0 + i,
                          end=120.0 + i, score=52.0, decision="below_min_score", features=_feat()) for i in range(6)]
        db.add_all(rows)
        db.flush()
        picked = sampler.select_for_review(db, rows, 55.0, 10)
        # quota is checked against stored examples; create them as the pipeline would
        for c, st, pr in picked:
            dataset.create_example(db, stratum=st, candidate=c, priority=pr, min_score=55.0)
            db.flush()
        n = db.scalar(select(__import__("sqlalchemy").func.count()).select_from(EvalExample).where(
            EvalExample.session_id == sess_id, EvalExample.stratum == "near_threshold"))
        assert 1 <= n <= sampler.QUOTA["near_threshold"] + 5
        assert sampler.stratum_of(Candidate(id=1, decision="text_only", features={}, score=None, peak=1), 55) in (
            None, ("conversation", 20.0))
        game = Candidate(id=7, decision="below_min_score", score=20.0, peak=5, features=_feat(game_only=1))
        assert sampler.stratum_of(game, 55.0)[0] == "noise_no_reaction"
        laugh = Candidate(id=8, decision="below_min_score", score=10.0, peak=5, features=_feat(n_laughter=2))
        assert sampler.stratum_of(laugh, 55.0)[0] == "rejected_reaction"


def _example(db, sess_id, *, decision, score, stratum="accepted", is_test=False, split=None, start=10.0, end=40.0):
    s = db.get(StreamSession, sess_id)
    cand = Candidate(session_id=sess_id, streamer_id=s.streamer_id, peak=start + 10, start=start, end=end, score=score,
                     decision=decision, features=_feat(score=score, dim_reaction=score / 10))
    db.add(cand)
    db.flush()
    ex = dataset.create_example(db, stratum=stratum, candidate=cand, priority=50.0, min_score=55.0)
    ex.is_test = is_test
    ex.media_state = "ready"
    if split:
        ex.split = split
    db.flush()
    return ex


def test_review_api_validation_save_metrics_and_export(admin):
    _, sess_id = _session("review")
    with session_scope() as db:
        good = _example(db, sess_id, decision="accepted", score=80.0).id
        bad = _example(db, sess_id, decision="accepted", score=70.0, start=100, end=130).id
        fn = _example(db, sess_id, decision="below_min_score", score=50.0, stratum="near_threshold",
                      start=200, end=230).id
        test = _example(db, sess_id, decision="accepted", score=90.0, is_test=True, start=300, end=330).id
    assert admin.get("/api/review/taxonomy").json()["ratings"]["should_not_exist"]
    # invalid inputs are refused
    assert admin.put(f"/api/review/examples/{good}/review", json={"rating": "meh"}, headers=CSRF).status_code == 422
    assert admin.put(f"/api/review/examples/{good}/review", json={"rating": "good", "editorial_reasons": ["x"]},
                     headers=CSRF).status_code == 422
    assert admin.put(f"/api/review/examples/{good}/review",
                     json={"rating": "good", "short_start": 5000.0, "short_end": 5030.0}, headers=CSRF).status_code == 422
    r = admin.put(f"/api/review/examples/{good}/review",
                  json={"rating": "excellent", "category": "crash", "short_start": 8.0, "short_end": 38.0,
                        "event_start": 9.0, "event_peak": 20.0, "event_end": 35.0, "context_quality": 8},
                  headers=CSRF)
    assert r.status_code == 200, r.text
    assert r.json()["review"]["rating"] == "excellent" and r.json()["reviewed_at"]
    admin.put(f"/api/review/examples/{bad}/review",
              json={"rating": "should_not_exist", "editorial_reasons": ["nothing_interesting", "false_reaction"]},
              headers=CSRF)
    admin.put(f"/api/review/examples/{fn}/review", json={"rating": "good", "editorial_reasons": ["undervalued"]},
              headers=CSRF)
    admin.put(f"/api/review/examples/{test}/review", json={"rating": "excellent"}, headers=CSRF)
    with session_scope() as db:
        m = metrics.editorial(db)
        rv = db.scalar(select(Review).where(Review.example_id == good))
        assert rv.ai_snapshot["score"] == 80.0  # the judged AI result is frozen with the label
    assert m["false_negatives_known"] >= 1 and m["false_positives"] >= 1
    assert m["precision"] is not None and 0 < m["precision"] < 1
    assert all(e["example_id"] != test for e in m["false_positive_examples"])  # test clips are excluded
    assert m["start_error_s"]["n"] >= 1
    jl = admin.get("/api/review/export?format=jsonl")
    assert jl.status_code == 200
    rows = [json.loads(line) for line in jl.text.splitlines()]
    row = next(x for x in rows if x["example_id"] == test)
    assert row["is_test"] and row["usable_as_positive"] is False
    assert next(x for x in rows if x["example_id"] == good)["label"]["positive"] is True
    csv_ = admin.get("/api/review/export?format=csv").text
    assert csv_.splitlines()[0].startswith("example_id,split")
    assert admin.delete(f"/api/review/examples/{good}/review", headers=CSRF).status_code == 200
    assert admin.get(f"/api/review/examples/{good}").json()["reviewed_at"] is None


def test_ranker_refuses_small_data_then_trains_and_needs_evidence_to_activate(admin):
    r = admin.post("/api/review/rankers/train", headers=CSRF)
    assert r.status_code == 422 and "insuficientes" in r.json()["detail"]["message"]
    _, s1 = _session("rk1")
    _, s2 = _session("rk2")
    with session_scope() as db:
        # positives have strong reactions; the heuristic score is made uninformative (constant)
        for i in range(40):
            pos = i % 2 == 0
            ex = _example(db, s1, decision="accepted" if pos else "below_min_score", score=60.0,
                          start=10.0 + i * 40, end=40.0 + i * 40, split="train")
            cand = db.get(Candidate, ex.candidate_id)
            cand.features = _feat(n_laughter=3 if pos else 0, dim_reaction=8.0 if pos else 1.0)
            db.add(Review(example_id=ex.id, reviewer="t@x", rating="good" if pos else "bad"))
            ex.reviewed_at = utcnow()
        for i in range(12):
            pos = i % 2 == 0
            ex = _example(db, s2, decision="accepted", score=60.0, start=10.0 + i * 40, end=40.0 + i * 40,
                          split="val")
            cand = db.get(Candidate, ex.candidate_id)
            cand.features = _feat(n_laughter=3 if pos else 0, dim_reaction=8.0 if pos else 1.0)
            db.add(Review(example_id=ex.id, reviewer="t@x", rating="excellent" if pos else "should_not_exist"))
            ex.reviewed_at = utcnow()
    r = admin.post("/api/review/rankers/train", headers=CSRF)
    assert r.status_code == 200, r.text
    m = r.json()
    assert m["status"] == "candidate" and m["metrics"]["ranker"]["auc"] >= 0.8
    assert m["metrics"]["ranker"]["auc"] > m["metrics"]["heuristic"]["auc"] and m["metrics"]["beats_heuristic"]
    assert admin.post(f"/api/review/rankers/{m['id']}/status", json={"status": "shadow"}, headers=CSRF).status_code == 200
    with session_scope() as db:
        model = ranker.scoring_model(db)
        p_pos = ranker.predict(model.params, model.feature_names, _feat(n_laughter=3, dim_reaction=8.0))
        p_neg = ranker.predict(model.params, model.feature_names, _feat())
        assert p_pos > 0.5 > p_neg
        # a model that did not beat the heuristic can never be activated
        model.metrics = {**model.metrics, "beats_heuristic": False}
    assert admin.post(f"/api/review/rankers/{m['id']}/status", json={"status": "active"},
                      headers=CSRF).status_code == 422
    with session_scope() as db:
        for mdl in db.scalars(select(__import__("clipper.models", fromlist=["RankerModel"]).RankerModel)):
            mdl.status = "archived"  # leave production-like state for the other tests


def test_retention_by_age_and_quota_keeps_labels(client, tmp_path, monkeypatch):
    _, sess_id = _session("ret")
    from clipper import storage

    monkeypatch.setattr(storage, "root", lambda: tmp_path)
    with session_scope() as db:
        exs = []
        for i in range(3):
            ex = _example(db, sess_id, decision="accepted", score=70.0, start=10.0 + i * 50, end=40.0 + i * 50)
            p = tmp_path / f"e{i}.mp4"
            p.write_bytes(b"x" * 1000)
            ex.media_path, ex.media_bytes = p.name, 1000
            exs.append(ex)
        exs[0].created_at = utcnow() - timedelta(days=40)  # too old
        db.add(Review(example_id=exs[2].id, reviewer="t@x", rating="good"))
        exs[2].reviewed_at = utcnow()
        db.flush()
        out = enforce_retention(db, AISettings(dataset_retention_days=21, dataset_quota_gb=1.0))
        assert out["expired"] >= 1
        assert exs[0].media_state == "expired" and not (tmp_path / "e0.mp4").exists()
        assert exs[2].media_state == "ready"  # reviewed + recent: kept
        assert db.scalar(select(Review).where(Review.example_id == exs[2].id)) is not None
