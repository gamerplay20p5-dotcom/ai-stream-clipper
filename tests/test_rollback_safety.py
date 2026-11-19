"""PR #2 final review: late failures of old renders, and a safe rollback of migration 0010."""

from __future__ import annotations

from datetime import datetime

import sqlalchemy as sa
from sqlalchemy import select

from clipper import pipeline, pipeline_structure as ps
from clipper.db import session_scope
from clipper.migrate import downgrade_db, upgrade_db
from clipper.models import Clip, Job, Publication, PublishAccount, Streamer, StreamSession
from clipper.publish import rollback
from clipper.publish.editorial import set_paused
from clipper.settings_store import load_ai_settings


def _clip(tag: str) -> int:
    with session_scope() as db:
        s = Streamer(platform="twitch", channel=f"rb_{tag}", url=f"https://www.twitch.tv/rb_{tag}")
        db.add(s)
        db.flush()
        sess = StreamSession(streamer_id=s.id, status="ended")
        db.add(sess)
        db.flush()
        c = Clip(session_id=sess.id, streamer_id=s.id, start=100, end=140, peak=130, score=80, status="ready",
                 kinds=["laughter"], version=1, video_path=f"clips/{tag}/clip_v1_aaaa.mp4", sha256="a" * 64)
        db.add(c)
        db.flush()
        return c.id


def _dead(db, job: Job, error="ffmpeg killed"):
    job.last_error = error
    pipeline.on_job_dead(db, job)


# ------------------------------------------------------------------ 1. late failures
def test_late_failure_of_an_old_variant_render_never_fails_the_newer_one(client):
    cid = _clip("var")
    with session_scope() as db:
        c = db.get(Clip, cid)
        ps.plan_variant(db, c, load_ai_settings(db), "D", manual=True)
        old_rid = c.provenance["variants"]["edit"]["render_id"]
        ps.plan_variant(db, c, load_ai_settings(db), "A", manual=True)  # newer request queued
        new_rid = c.provenance["variants"]["edit"]["render_id"]
        jobs = {j.payload["render_id"]: j for j in db.scalars(select(Job).where(Job.type == "render"))
                if j.payload.get("clip_id") == cid}
        _dead(db, jobs[old_rid])  # the OLD one dies after the new one was queued
        rec = db.get(Clip, cid).provenance["variants"]["edit"]
        assert rec["state"] == "queued" and rec["render_id"] == new_rid and rec["structure"] == "A"
        _dead(db, jobs[new_rid])  # the current one failing IS reported
        assert db.get(Clip, cid).provenance["variants"]["edit"]["state"] == "failed"


def test_late_failure_of_an_old_main_render_never_fails_the_newer_version(client):
    cid = _clip("main")
    with session_scope() as db:
        c = db.get(Clip, cid)
        old_job = pipeline.enqueue_render(db, c)
        c.version += 1  # e.g. an edit + re-render while the first one is still running
        new_job = pipeline.enqueue_render(db, c)
        assert old_job.id != new_job.id
        _dead(db, old_job)
        assert db.get(Clip, cid).status == "rendering"  # still waiting for the new one
        _dead(db, new_job)
        assert db.get(Clip, cid).status == "failed"


def test_same_version_but_other_render_id_is_also_ignored(client):
    cid = _clip("rid")
    with session_scope() as db:
        c = db.get(Clip, cid)
        pipeline.enqueue_render(db, c)
        fake = Job(type="render", payload={"clip_id": cid, "version": c.version, "render_id": "deadbeef0000"},
                   status="dead")
        assert pipeline.mark_render_failed(db, cid, "x", version=c.version, render_id="deadbeef0000") is False
        assert db.get(Clip, cid).status == "rendering"
        del fake


# ------------------------------------------------------------------ 2. rollback of 0010
def _rows(eng, sql):
    with eng.begin() as c:
        return c.execute(sa.text(sql)).all()


def test_downgrade_keeps_publication_snapshots_and_upgrade_restores_them(tmp_path):
    url = f"sqlite:///{tmp_path / 'm.db'}"
    upgrade_db(url, "head")
    eng = sa.create_engine(url)
    md = sa.MetaData()
    md.reflect(eng)
    with eng.begin() as c:
        c.execute(sa.text("PRAGMA foreign_keys=OFF"))
        cols = {col.name: col for col in md.tables["publications"].columns}
        vals = {}
        for name, col in cols.items():
            if col.primary_key:
                continue
            kind = str(col.type).upper()
            vals[name] = (datetime(2026, 11, 19) if "DATE" in kind or "TIME" in kind else {} if "JSON" in kind
                          else 0 if ("INT" in kind or "BOOL" in kind) else "x")
        vals.update({"id": 7, "account_id": 1, "status": "scheduled", "video_path": "clips/1/clip_v1_abc.mp4",
                     "clip_id": None})
        c.execute(md.tables["publications"].insert().values(**vals))
    downgrade_db(url, "0009")
    assert "publication_snapshots_preserved" in sa.inspect(eng).get_table_names()
    assert _rows(eng, "SELECT publication_id, video_path FROM publication_snapshots_preserved") == [
        (7, "clips/1/clip_v1_abc.mp4")]
    upgrade_db(url, "head")
    assert _rows(eng, "SELECT video_path FROM publications WHERE id = 7") == [("clips/1/clip_v1_abc.mp4",)]
    assert "publication_snapshots_preserved" not in sa.inspect(eng).get_table_names()


def test_rollback_check_needs_the_pause_and_cancels_mismatched_pending_publications(client):
    cid, cid2 = _clip("chk"), _clip("chk2")
    with session_scope() as db:
        a = PublishAccount(platform="youtube", external_id="rb-acc", status="connected", connected_by="t@x")
        db.add(a)
        db.flush()
        same = Publication(clip_id=cid, account_id=a.id, platform="youtube", status="scheduled", privacy="private",
                           video_path="clips/chk/clip_v1_aaaa.mp4", video_sha256="a" * 64)
        db.add(same)
        db.flush()
        stale = Publication(clip_id=cid2, account_id=a.id, platform="youtube", status="scheduled", privacy="private",
                            video_path="clips/chk2/clip_v1_OLD.mp4", video_sha256="b" * 64)  # promoted since
        db.add(stale)
        db.flush()
        same_id, stale_id = same.id, stale.id
        set_paused(db, False, "", "t")
        out = rollback.check(db)
        assert [r["publication_id"] for r in out["mismatched"] if r["publication_id"] in (same_id, stale_id)] == [stale_id]
        assert out["safe_to_rollback"] is False  # not paused yet
        set_paused(db, True, "rollback 0010", "t")
        done = rollback.cancel_mismatched(db, "t")
        assert stale_id in done and same_id not in done
        assert db.get(Publication, stale_id).status == "cancelled" and db.get(Publication, same_id).status == "scheduled"
        assert rollback.check(db)["safe_to_rollback"] is True
        set_paused(db, False, "", "t")


def test_cli_pause_and_check(client, capsys):
    from clipper import cli

    cli.main(["publish-pause", "on", "--reason", "rollback"])
    assert '"paused": true' in capsys.readouterr().out
    try:
        cli.main(["rollback-check", "--cancel-mismatched"])
    except SystemExit as e:
        assert e.code in (0, 1)
    assert '"safe_to_rollback"' in capsys.readouterr().out
    cli.main(["publish-pause", "off"])
    assert '"paused": false' in capsys.readouterr().out
