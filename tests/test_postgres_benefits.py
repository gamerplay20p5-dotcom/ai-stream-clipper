"""HTS access benefits on a REAL PostgreSQL with concurrent connections (throwaway server, tests/pg_support.py).

* ten simultaneous requests with the same request key create ONE credit (unique key + savepoint);
* ten simultaneous temporary upgrades with different keys: exactly one becomes active (account rows locked);
* a credit given while a renewal snapshot arrives is never lost and never doubled.
Skipped without ASC_TEST_PG_URL, FAILED under ASC_REQUIRE_PG=1.
"""

from __future__ import annotations

import uuid

import pytest
import sqlalchemy as sa

from test_postgres_shared_lives import _customer, _parallel, _run, _snapshot, pg  # noqa: F401

pytestmark = pytest.mark.postgres


def _try(make, fn):
    from clipper import benefits

    try:
        return _run(make, fn)
    except benefits.BenefitError as exc:
        return f"refused: {exc}"


def test_same_request_key_from_ten_connections_creates_one_credit(pg):
    from clipper import benefits, entitlements
    from clipper.models import AccessBenefit

    engine, make = pg
    uid = _customer(make, "creator")
    key = uuid.uuid4().hex
    results = _parallel(10, lambda i: _try(make, lambda db: benefits.add_credit(
        db, user_id=uid, days=3, reason="queda do servidor", request_key=key, admin="admin@hts")[0].id))
    assert len(set(results)) == 1 and isinstance(results[0], int), results
    with make() as db:
        assert db.scalar(sa.select(sa.func.count()).select_from(AccessBenefit).where(AccessBenefit.user_id == uid)) == 1
        assert entitlements.current(db, uid).credit_days == 3


def test_concurrent_upgrades_leave_exactly_one_active(pg):
    from clipper import benefits, entitlements
    from clipper.models import AccessBenefit

    engine, make = pg
    uid = _customer(make, "creator")
    results = _parallel(10, lambda i: _try(make, lambda db: benefits.add_upgrade(
        db, user_id=uid, plan="pro", days=7, reason="teste do Pro", request_key=uuid.uuid4().hex, admin="admin@hts")[0].id))
    created = [r for r in results if isinstance(r, int)]
    assert len(created) == 1, results
    assert all("já existe um upgrade" in r for r in results if isinstance(r, str)), results
    with make() as db:
        assert db.scalar(sa.select(sa.func.count()).select_from(AccessBenefit).where(
            AccessBenefit.user_id == uid, AccessBenefit.kind == "upgrade")) == 1
        e = entitlements.current(db, uid)
        assert (e.plan, e.base_plan) == ("pro", "creator")


def test_credit_and_renewal_at_the_same_time(pg):
    from clipper import benefits, entitlements

    engine, make = pg
    uid = _customer(make, "creator")

    def work(i):
        if i == 0:
            return _try(make, lambda db: benefits.add_credit(db, user_id=uid, days=5, reason="compensação",
                                                             request_key=uuid.uuid4().hex, admin="admin@hts")[0].id)
        return _run(make, lambda db: _snapshot(db, uid, "creator", seq=2, until_days=60))
    _parallel(2, work)
    with make() as db:
        e = entitlements.current(db, uid)
        sub = db.get(__import__("clipper.models", fromlist=["AccountSubscription"]).AccountSubscription, e.subscription_id)
        assert e.credit_days == 5
        assert e.until == sub.access_until + __import__("datetime").timedelta(days=5), "renewed date + the credit, once"
