"""Migrations 0015 -> 0020 on a REAL PostgreSQL (throwaway server, see tests/pg_support.py).

* fresh install up to head, with the expected tables, columns and indexes;
* the chain step by step from 0014 (the version in production) to 0020, every downgrade back, and up again;
* a representative database at 0014 upgraded to head with its data preserved;
* 0020 filling the benefit columns of existing entitled subscriptions and enforcing one benefit per request key;
* 0018 refusing duplicate 'live' sessions BEFORE any change: version, schema and data intact, then success.

Each scenario takes only a database URL; the tests below run them on PostgreSQL databases created and dropped per
test. Skipped without ASC_TEST_PG_URL, FAILED under ASC_REQUIRE_PG=1.
"""

from __future__ import annotations

import pytest
import sqlalchemy as sa

pytestmark = pytest.mark.postgres

CHAIN = ["0015", "0016", "0017", "0018", "0019", "0020"]
HEAD = CHAIN[-1]
HEAD_TABLES = {"password_tokens", "account_subscriptions", "billing_events", "usage_entries", "channel_monitors",
               "monitor_sessions", "entitlement_periods", "customer_publish_settings", "publish_units", "courtesy_grants",
               "access_benefits"}
HEAD_COLUMNS = {
    "streamers": {"owner_id", "hts_monitoring"},
    "stream_sessions": {"base_profile"},
    "clip_shares": {"monitor_id"},
    "usage_entries": {"period_id"},
    "publish_accounts": {"owner_id", "paused_reason"},
    "publish_rules": {"owner_id", "rights_confirmed_at", "rights_note"},
    "publications": {"owner_id"},
    "oauth_states": {"owner_id"},
    "publish_units": {"reserved_at"},
    "account_subscriptions": {"last_entitled_until", "last_period_anchor"},
}
HEAD_INDEXES = {
    "stream_sessions": {"uq_stream_sessions_one_live"},
    "channel_monitors": {"uq_channel_monitors_active"},
    "publish_units": {"ix_publish_units_user_day"},
    "access_benefits": {"ix_access_benefits_user"},
}


def _version(url: str) -> str:
    eng = sa.create_engine(url)
    with eng.connect() as c:
        v = c.execute(sa.text("SELECT version_num FROM alembic_version")).scalar()
    eng.dispose()
    return v


def _inspect(url: str):
    eng = sa.create_engine(url)
    insp = sa.inspect(eng)
    out = {"tables": set(insp.get_table_names()), "columns": {}, "indexes": {}}
    for t in out["tables"]:
        out["columns"][t] = {c["name"] for c in insp.get_columns(t)}
        out["indexes"][t] = {i["name"]: i for i in insp.get_indexes(t)}
    eng.dispose()
    return out


def _downgrade(url: str, rev: str) -> None:
    from alembic import command

    from clipper.migrate import alembic_config

    command.downgrade(alembic_config(url), rev)


# ---------------------------------------------------------------- scenarios (take only a URL)
def scenario_fresh_install(url: str) -> None:
    from clipper.migrate import upgrade_db

    upgrade_db(url)
    assert _version(url) == HEAD
    s = _inspect(url)
    assert HEAD_TABLES <= s["tables"], HEAD_TABLES - s["tables"]
    for table, cols in HEAD_COLUMNS.items():
        assert cols <= s["columns"][table], (table, cols - s["columns"][table])
    for table, names in HEAD_INDEXES.items():
        assert names <= set(s["indexes"][table]), (table, names - set(s["indexes"][table]))
    assert s["indexes"]["stream_sessions"]["uq_stream_sessions_one_live"]["unique"]
    upgrade_db(url)  # running it again changes nothing
    assert _version(url) == HEAD


def scenario_chain_up_and_down(url: str) -> None:
    from clipper.migrate import upgrade_db

    upgrade_db(url, "0014")
    for rev in CHAIN:
        upgrade_db(url, rev)
        assert _version(url) == rev
    for rev in reversed(["0014"] + CHAIN[:-1]):
        _downgrade(url, rev)
        assert _version(url) == rev
    s = _inspect(url)
    assert not ({"channel_monitors", "publish_units", "account_subscriptions"} & s["tables"])
    assert "owner_id" not in s["columns"]["publish_accounts"]
    upgrade_db(url)
    assert _version(url) == HEAD


def seed_0014(url: str) -> dict:
    """A small but representative production-like database at 0014 (no real data)."""
    eng = sa.create_engine(url)
    with eng.begin() as c:
        for uid, email, role in ((1, "admin@example.test", "admin"), (2, "viewer@example.test", "viewer")):
            c.execute(sa.text("INSERT INTO users (id, email, password_hash, role, active, session_version, created_at) "
                              "VALUES (:i, :e, 'x', :r, :a, 0, CURRENT_TIMESTAMP)"), {"i": uid, "e": email, "r": role, "a": True})
        for sid, name, status in ((10, "hts_a", "live"), (11, "hts_b", "offline")):
            c.execute(sa.text("INSERT INTO streamers (id, platform, channel, url, display_name, enabled, preferences, status, "
                              "stream_title, last_error, check_requested, created_at) VALUES (:i, 'twitch', :n, :u, :n, :t, "
                              "CAST(:p AS JSON), :s, '', '', :f, CURRENT_TIMESTAMP)"),
                      {"i": sid, "n": name, "u": f"https://www.twitch.tv/{name}", "t": True, "p": "{}", "s": status, "f": False})
        for sess, sid, status in ((100, 10, "live"), (101, 10, "ended"), (102, 11, "ended")):
            c.execute(sa.text("INSERT INTO stream_sessions (id, streamer_id, status, title, started_at, end_reason, "
                              "capture_worker_id, segment_base_url, covered_until, analyzed_until, scored_until, segment_count, "
                              "stop_requested, stats) VALUES (:i, :s, :st, '', CURRENT_TIMESTAMP, '', '', '', 0, 0, 0, 0, :f, CAST(:j AS JSON))"),
                      {"i": sess, "s": sid, "st": status, "f": False, "j": "{}"})
    eng.dispose()
    return {"users": 2, "streamers": 2, "stream_sessions": 3}


def scenario_representative_upgrade(url: str) -> None:
    from clipper.migrate import upgrade_db

    upgrade_db(url, "0014")
    counts = seed_0014(url)
    upgrade_db(url)
    assert _version(url) == HEAD
    eng = sa.create_engine(url)
    with eng.connect() as c:
        for table, n in counts.items():
            assert c.execute(sa.text(f"SELECT COUNT(*) FROM {table}")).scalar() == n, table
        assert set(c.execute(sa.text("SELECT hts_monitoring FROM streamers")).scalars()) == {True}, "HTS behaviour unchanged"
        assert c.execute(sa.text("SELECT COUNT(*) FROM channel_monitors")).scalar() == 0
        assert c.execute(sa.text("SELECT COUNT(*) FROM stream_sessions WHERE status = 'live'")).scalar() == 1
    eng.dispose()


def scenario_duplicate_lives_refused(url: str) -> None:
    from clipper.migrate import upgrade_db

    upgrade_db(url, "0014")
    seed_0014(url)
    upgrade_db(url, "0017")
    eng = sa.create_engine(url)
    with eng.begin() as c:  # a second live session for streamer 10
        c.execute(sa.text("INSERT INTO stream_sessions (id, streamer_id, status, title, started_at, end_reason, "
                          "capture_worker_id, segment_base_url, covered_until, analyzed_until, scored_until, segment_count, "
                          "stop_requested, stats) VALUES (103, 10, 'live', '', CURRENT_TIMESTAMP, '', '', '', 0, 0, 0, 0, :f, CAST(:j AS JSON))"),
                  {"f": False, "j": "{}"})
    with pytest.raises(Exception) as err:
        upgrade_db(url)
    assert "uq_stream_sessions_one_live" in str(err.value) and "streamer 10: 2 sessões live (ids 100..103)" in str(err.value)
    assert _version(url) == "0017", "nothing applied"
    s = _inspect(url)
    assert "channel_monitors" not in s["tables"] and "hts_monitoring" not in s["columns"]["streamers"], "schema intact"
    with eng.begin() as c:
        assert c.execute(sa.text("SELECT COUNT(*) FROM stream_sessions WHERE status = 'live'")).scalar() == 2, "data intact"
        assert c.execute(sa.text("SELECT COUNT(*) FROM stream_sessions")).scalar() == 4
        c.execute(sa.text("UPDATE stream_sessions SET status = 'ended' WHERE id = 103"))  # the operator ends the duplicate
    eng.dispose()
    upgrade_db(url)
    assert _version(url) == HEAD
    assert _inspect(url)["indexes"]["stream_sessions"]["uq_stream_sessions_one_live"]["unique"]


def scenario_benefits_0020(url: str) -> None:
    """0019 -> 0020 with real subscriptions: entitled rows get their benefit columns, others stay empty; request keys
    are unique; the downgrade removes only what 0020 added."""
    from clipper.migrate import upgrade_db

    upgrade_db(url, "0019")
    eng = sa.create_engine(url)
    with eng.begin() as c:
        c.execute(sa.text("INSERT INTO users (id, email, password_hash, role, active, session_version, created_at) "
                          "VALUES (5, 'cliente@example.test', 'x', 'customer', :a, 0, CURRENT_TIMESTAMP)"), {"a": True})
        for sid, status, until in ((1, "active", "2030-01-31 00:00:00"), (2, "ended", None)):
            c.execute(sa.text("INSERT INTO account_subscriptions (id, user_id, provider, subscription_ref, plan, market, "
                              "financial_status, review, access_status, access_until, period_start, period_end, renews_at, "
                              "cancel_at_period_end, limits, manage, renewal, payments, last_seq, updated_at) VALUES (:i, 5, "
                              "'stripe', :r, 'creator', 'US', :s, '', :s, :u, '2030-01-01 00:00:00', NULL, NULL, :f, "
                              "CAST('{}' AS JSON), '', NULL, CAST('[]' AS JSON), 1, CURRENT_TIMESTAMP)"),
                      {"i": sid, "r": f"sub_{sid}", "s": status, "u": until, "f": False})
    upgrade_db(url, "0020")
    with eng.begin() as c:
        rows = dict(c.execute(sa.text("SELECT id, last_entitled_until IS NOT NULL FROM account_subscriptions")).all())
        assert rows == {1: True, 2: False}, rows
        assert str(c.execute(sa.text("SELECT last_period_anchor FROM account_subscriptions WHERE id = 1")).scalar()).startswith("2030-01-01")
        insert = ("INSERT INTO access_benefits (user_id, kind, days, plan, starts_at, reason, request_key, created_by, created_at) "
                  "VALUES (5, 'credit', 3, '', CURRENT_TIMESTAMP, 'queda', 'k-0123456789abcdef', 'admin', CURRENT_TIMESTAMP)")
        c.execute(sa.text(insert))
    with pytest.raises(sa.exc.IntegrityError):
        with eng.begin() as c:
            c.execute(sa.text(insert))
    with pytest.raises(sa.exc.IntegrityError):
        with eng.begin() as c:
            c.execute(sa.text(insert.replace("'credit', 3", "'credit', 999").replace("k-0123", "k-9999")))
    eng.dispose()
    _downgrade(url, "0019")
    s = _inspect(url)
    assert "access_benefits" not in s["tables"] and "last_entitled_until" not in s["columns"]["account_subscriptions"]
    assert "courtesy_grants" in s["tables"], "only 0020 is removed"
    upgrade_db(url)
    assert _version(url) == HEAD


# ---------------------------------------------------------------- PostgreSQL tests
@pytest.fixture
def pgdb():
    import pg_support

    from test_postgres_shared_lives import _safe_admin_url

    _safe_admin_url()  # skip / fail / refuse exactly like the other PostgreSQL tests
    name, url = pg_support.create_database("asc_test_mig")
    yield url
    pg_support.drop_database(name)


def test_fresh_install_postgres(pgdb):
    scenario_fresh_install(pgdb)


def test_chain_up_and_down_postgres(pgdb):
    scenario_chain_up_and_down(pgdb)


def test_representative_0014_database_upgrades_postgres(pgdb):
    scenario_representative_upgrade(pgdb)


def test_duplicate_lives_refused_without_corruption_postgres(pgdb):
    scenario_duplicate_lives_refused(pgdb)


def test_benefits_0020_postgres(pgdb):
    scenario_benefits_0020(pgdb)
