"""Billing link regressions: reconciliation endpoint, cancellation, catalog limits and plan tampering.

The commercial outbox side (outages, audited resend, supersession, leases) is covered in
commercial/server/outbox.test.js; these tests pin what the main API guarantees to it.
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path

from fastapi.testclient import TestClient

from test_billing_entitlements import (BILLING_SECRET, DAY, _now, billing_env, customer, deliver, owned_streamer,  # noqa: F401
                                       snapshot, sub_state)

CATALOG = json.loads((Path(__file__).resolve().parents[1] / "commercial" / "catalog.json").read_text(encoding="utf-8"))


def state(client, refs, *, secret=BILLING_SECRET, caller="commercial"):
    from clipper.security import sha256_bytes, sign_request

    body = json.dumps({"refs": refs}).encode()
    headers = sign_request(secret, caller, "POST", "/internal/billing/state", sha256_bytes(body))
    return client.post("/internal/billing/state", content=body, headers={**headers, "Content-Type": "application/json"})


def test_state_endpoint_reports_last_applied_seq_and_is_signed(app, client):
    uid, _ = customer(app)
    ref = f"sub_{uuid.uuid4().hex[:8]}"
    deliver(client, snapshot(uid, seq=4, ref=ref))
    deliver(client, snapshot(uid, seq=2, ref=ref, status="ended"))  # stale: ignored
    r = state(client, [{"provider": "stripe", "subscription_ref": ref}, {"provider": "infinitepay", "subscription_ref": "nope"}])
    assert r.status_code == 200, r.text
    [item] = r.json()["items"]
    assert (item["last_seq"], item["access_status"], item["account_id"]) == (4, "active", uid)

    refs = [{"provider": "stripe", "subscription_ref": ref}]
    assert state(client, refs, secret="wrong-secret-0123456789abcdefghijk").status_code == 401
    assert state(client, refs, caller="main").status_code == 401
    assert client.post("/internal/billing/state", json={"refs": refs}).status_code == 401
    assert state(client, [{"provider": "paypal", "subscription_ref": "x"}]).status_code == 422
    assert state(client, [{"provider": "stripe", "subscription_ref": "x"}] * 501).status_code == 422


def test_failure_after_a_confirmed_payment_is_recovered_by_redelivery(app, client):
    """The API was down when the payment was confirmed: the same event delivered later activates exactly once."""
    uid, c = customer(app)
    paid = snapshot(uid, seq=1)
    assert sub_state(c)["entitled"] is False  # nothing arrived yet: no access, no partial state
    assert deliver(client, paid).json() == {"outcome": "applied"}
    assert deliver(client, paid).json() == {"outcome": "duplicate"}  # outbox retry after a lost response
    assert sub_state(c)["entitled"] is True


def test_cancellation_keeps_access_until_period_end_then_ends(app, client):
    uid, c = customer(app)
    now = _now()
    deliver(client, snapshot(uid, seq=1))
    canceling = snapshot(uid, seq=2, until=now + 10 * DAY)
    canceling["access"]["cancel_at_period_end"] = True
    deliver(client, canceling)
    s = sub_state(c)
    assert s["entitled"] is True and s["subscription"]["cancel_at_period_end"] is True
    deliver(client, snapshot(uid, seq=3, status="ended", financial="canceled", until=now - 1))
    assert sub_state(c)["entitled"] is False
    # A late "active" snapshot from before the cancellation never revives it
    assert deliver(client, snapshot(uid, seq=2, status="active")).json() == {"outcome": "stale"}
    assert sub_state(c)["entitled"] is False


def test_creator_keeps_two_simultaneous_lives_until_the_owner_decides():
    """Do not change Creator from 2 to 1 silently: the value and the pending decision are pinned here."""
    creator = CATALOG["plans"]["creator"]
    assert creator["simultaneous"] == 2
    assert CATALOG["decisions"]["creatorSimultaneous"].startswith("PENDING")
    assert [CATALOG["plans"][p]["simultaneous"] for p in ("basic", "creator", "pro")] == [1, 2, 2]


def test_limits_come_from_the_signed_snapshot_not_the_browser(app, client):
    uid, c = customer(app)
    deliver(client, snapshot(uid, seq=1, plan="basic"))
    csrf = {"X-ASC-CSRF": "1"}
    # No route accepts a plan, limit or status from the browser
    for method, path, body in [
        ("post", "/api/account/subscription", {"plan": "pro"}),
        ("patch", "/api/account/subscription", {"limits": {"channels": 99}}),
        ("put", "/api/account/subscription", {"access_status": "active"}),
        ("post", "/internal/billing/events", snapshot(uid, seq=9, plan="pro")),
    ]:
        r = getattr(c, method)(path, json=body, headers=csrf)
        assert r.status_code in (401, 403, 404, 405), (method, path, r.status_code)
    s = sub_state(c)
    assert s["subscription"]["plan"] == "basic" and s["limits"]["channels"] == 1


def test_customer_cannot_read_or_change_another_customers_channels(app, client):
    uid_a, a = customer(app)
    uid_b, b = customer(app)
    deliver(client, snapshot(uid_a, seq=1))
    deliver(client, snapshot(uid_b, seq=1))
    sid_b = owned_streamer(uid_b)
    assert all(ch["id"] != sid_b for ch in a.get("/api/account/channels").json())
    assert a.delete(f"/api/account/channels/{sid_b}", headers={"X-ASC-CSRF": "1"}).status_code == 404
    assert len(b.get("/api/account/channels").json()) == 1
    # Staff-only and anonymous access stay closed
    assert TestClient(app).get("/api/account/subscription").status_code == 401
