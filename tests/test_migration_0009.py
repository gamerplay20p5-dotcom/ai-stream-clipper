"""Migration 0009 (quality gate): integrity, data preservation, downgrade and image rollback."""

from __future__ import annotations

from datetime import datetime

import sqlalchemy as sa
from alembic.script import ScriptDirectory

from clipper.migrate import alembic_config, downgrade_db, upgrade_db


def test_single_head_and_chain(tmp_path):
    url = f"sqlite:///{tmp_path / 'm.db'}"
    script = ScriptDirectory.from_config(alembic_config(url))
    assert len(script.get_heads()) == 1  # a single, linear chain
    assert script.get_revision("0009").down_revision == "0008"
    assert script.get_revision("0010").down_revision == "0009"


def test_upgrade_keeps_rows_downgrade_and_reupgrade(tmp_path):
    url = f"sqlite:///{tmp_path / 'm.db'}"
    upgrade_db(url, "0008")
    eng = sa.create_engine(url)
    md = sa.MetaData()
    md.reflect(eng)

    def row(table, **given):  # fill every NOT NULL column without a server default with a dummy value
        vals = dict(given)
        for col in md.tables[table].columns:
            if col.name in vals or col.nullable or col.server_default is not None or col.primary_key:
                continue
            kind = str(col.type).upper()
            if "DATE" in kind or "TIME" in kind:
                vals[col.name] = datetime(2026, 11, 18)
            elif "JSON" in kind:
                vals[col.name] = {}
            elif "INT" in kind or "BOOL" in kind:
                vals[col.name] = 0
            elif "FLOAT" in kind or "REAL" in kind or "NUMERIC" in kind:
                vals[col.name] = 0.0
            else:
                vals[col.name] = "x"
        return vals

    with eng.begin() as c:
        c.execute(md.tables["streamers"].insert().values(row("streamers", id=1)))
        c.execute(md.tables["stream_sessions"].insert().values(row("stream_sessions", id=1, streamer_id=1)))
        c.execute(md.tables["clips"].insert().values(row("clips", id=1, session_id=1, streamer_id=1, title="antes")))
    upgrade_db(url, "head")
    insp = sa.inspect(eng)
    cols = {c["name"]: c for c in insp.get_columns("clips")}
    assert "gate" in cols and not cols["gate"]["nullable"]
    assert any(ix["name"] == "ix_clips_gate" for ix in insp.get_indexes("clips"))
    with eng.begin() as c:
        assert c.execute(sa.text("SELECT title, gate FROM clips WHERE id = 1")).one() == ("antes", "")
    downgrade_db(url, "0008")  # also removes 0010 (edit_comparisons) on the way down
    assert "edit_comparisons" not in sa.inspect(eng).get_table_names()
    assert "gate" not in {c["name"] for c in sa.inspect(eng).get_columns("clips")}
    with eng.begin() as c:
        assert c.execute(sa.text("SELECT title FROM clips WHERE id = 1")).scalar() == "antes"  # data kept
    upgrade_db(url, "head")
    assert "gate" in {c["name"] for c in sa.inspect(eng).get_columns("clips")}


def test_old_code_can_still_insert_clips_after_the_upgrade(tmp_path):
    """Image rollback to v0.5.7 WITHOUT a DB downgrade: the old code does not know the column;
    the server default ('') must fill it."""
    url = f"sqlite:///{tmp_path / 'm.db'}"
    upgrade_db(url, "head")
    eng = sa.create_engine(url)
    cols = {c["name"]: c for c in sa.inspect(eng).get_columns("clips")}
    assert str(cols["gate"]["default"]).strip("'\"") == ""


def test_image_rollback_needs_the_db_downgrade_first(tmp_path):
    """The API runs `alembic upgrade head` at startup. An OLD image (no 0009 script) facing a DB at
    0009 cannot start -> the documented rollback downgrades with the NEW image first."""
    import shutil

    import pytest
    from alembic import command
    from alembic.config import Config

    from clipper.migrate import MIGRATIONS

    url = f"sqlite:///{tmp_path / 'm.db'}"
    upgrade_db(url, "head")
    old = tmp_path / "old_migrations"
    shutil.copytree(MIGRATIONS, old, ignore=shutil.ignore_patterns("0009_*", "0010_*", "0011_*", "__pycache__"))
    cfg = Config()
    cfg.set_main_option("script_location", str(old))
    cfg.set_main_option("sqlalchemy.url", url)
    with pytest.raises(Exception):
        command.upgrade(cfg, "head")  # what v0.5.7 would do at startup
    downgrade_db(url, "0008")  # step 1 of the rollback, run with the newer image
    command.upgrade(cfg, "head")  # now the old image starts normally


def test_0010_downgrade_preserves_human_votes_and_upgrade_restores_them(tmp_path):
    url = f"sqlite:///{tmp_path / 'm.db'}"
    upgrade_db(url, "head")
    eng = sa.create_engine(url)
    with eng.begin() as c:
        c.execute(sa.text("PRAGMA foreign_keys=OFF"))
        c.execute(sa.text("INSERT INTO edit_comparisons (id, clip_id, left, right, left_structure, right_structure, "
                          "winner, blind, reviewer, note, created_at) VALUES (1, 1, 'main', 'edit', 'O', 'A', 'right', "
                          "1, 'humano', '', '2026-11-19 00:00:00')"))
    downgrade_db(url, "0009")
    names = sa.inspect(eng).get_table_names()
    assert "edit_comparisons" not in names and "edit_comparisons_preserved" in names
    assert "video_path" not in {c["name"] for c in sa.inspect(eng).get_columns("publications")}
    upgrade_db(url, "head")
    with eng.begin() as c:
        assert c.execute(sa.text("SELECT winner, reviewer FROM edit_comparisons WHERE id = 1")).one() == ("right", "humano")
    assert "edit_comparisons_preserved" not in sa.inspect(eng).get_table_names()
