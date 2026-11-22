"""Confirmed payment -> plan active in the panel, across the two real processes (no network, no money).

The main API runs here (uvicorn on a free port, unit SQLite database). The REAL commercial app runs as a Node process
(commercial/integration/activation-harness.mjs) with Stripe and InfinitePay simulated. Everything between them is the
production path: the customer's checkout goes API -> commercial over signed HTTP, the provider confirmation goes
through the commercial webhooks, and the entitlement snapshot comes back commercial -> API through the outbox.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import threading
import time
from pathlib import Path

import httpx
import pytest

from conftest import CSRF
from test_billing_entitlements import BILLING_SECRET, billing_env, customer, sub_state  # noqa: F401

ROOT = Path(__file__).resolve().parents[1]
HARNESS = ROOT / "commercial" / "integration" / "activation-harness.mjs"
NODE = shutil.which("node")

pytestmark = pytest.mark.skipif(NODE is None or not (ROOT / "commercial" / "node_modules").is_dir(),
                                reason="needs Node.js and commercial/node_modules (npm ci)")


@pytest.fixture
def api_url(app):
    import uvicorn

    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=0, log_level="warning", lifespan="off"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.time() + 20
    while not server.started:
        assert time.time() < deadline, "API did not start"
        time.sleep(0.05)
    port = server.servers[0].sockets[0].getsockname()[1]
    yield f"http://127.0.0.1:{port}"
    server.should_exit = True
    thread.join(10)


def _harness(api: str, *, open_: bool):
    env = {**os.environ, "ASC_MAIN_API_URL": api, "ASC_BILLING_SECRET": BILLING_SECRET, "PAYMENTS_OPEN": "true" if open_ else "false"}
    proc = subprocess.Popen([NODE, str(HARNESS)], cwd=HARNESS.parents[1], env=env, stdin=subprocess.PIPE,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    line = proc.stdout.readline()
    if not line:
        proc.kill()
        raise AssertionError(f"harness failed: {proc.stderr.read()}")
    ports = json.loads(line)
    return proc, f"http://127.0.0.1:{ports['app']}", f"http://127.0.0.1:{ports['control']}"


@pytest.fixture
def commercial(api_url, monkeypatch):
    """Starts the commercial harness and points the API at it. Call with open_=True/False."""
    from clipper.config import reset_settings_cache

    procs = []

    def start(open_: bool = True):
        proc, app_url, control = _harness(api_url, open_=open_)
        procs.append(proc)
        monkeypatch.setenv("ASC_COMMERCIAL_URL", app_url)
        reset_settings_cache()
        return control

    yield start
    for proc in procs:
        proc.stdin.close()
        try:
            proc.wait(10)
        except subprocess.TimeoutExpired:
            proc.kill()
    reset_settings_cache()


def _control(control: str, path: str, body: dict | None = None) -> dict:
    r = httpx.post(f"{control}{path}", json=body or {}, timeout=30)
    assert r.status_code == 200, r.text
    return r.json()


def test_stripe_payment_activates_the_plan_in_the_panel(app, client, commercial):
    control = commercial(True)
    uid, c = customer(app)
    assert sub_state(c)["checkout_available"] is True
    r = c.post("/api/account/checkout", json={"plan": "pro", "market": "US", "language": "en"}, headers=CSRF)
    assert r.status_code == 201, r.text
    assert r.json()["url"].startswith("https://checkout.stripe.com/")
    # Returning from the checkout grants nothing: only the provider's confirmation does
    assert sub_state(c)["entitled"] is False

    out = _control(control, "/stripe/pay", {"account_id": uid, "plan": "pro"})
    assert out["webhook"] == 200
    state = sub_state(c)
    assert state["entitled"] is True, state
    assert (state["subscription"]["plan"], state["subscription"]["provider"], state["subscription"]["status"]) == ("pro", "stripe", "active")
    assert state["limits"]["channels"] == 4

    # Reconciliation: the commercial service reads back, signed, what the API applied
    items = _control(control, "/state", {"refs": [{"provider": "stripe", "subscription_ref": out["subscription"]}]})["items"]
    assert [(i["account_id"], i["plan"], i["access_status"]) for i in items] == [(uid, "pro", "active")]


def test_infinitepay_payment_activates_and_unpaid_charge_does_not(app, client, commercial):
    control = commercial(True)
    paid_uid, paid = customer(app)
    unpaid_uid, unpaid = customer(app)
    for c in (paid, unpaid):
        r = c.post("/api/account/checkout", json={"plan": "creator", "market": "BR", "language": "pt-BR"}, headers=CSRF)
        assert r.status_code == 201, r.text
        assert "infinitepay.io" in r.json()["url"]

    # A notice for a charge that payment_check reports as unpaid changes nothing
    assert _control(control, "/infinitepay/notice", {"account_id": unpaid_uid})["result"] != "confirmed"
    assert sub_state(unpaid)["entitled"] is False

    out = _control(control, "/infinitepay/pay", {"account_id": paid_uid})
    assert (out["webhook"], out["result"]) == (200, "confirmed")
    state = sub_state(paid)
    assert state["entitled"] is True, state
    assert (state["subscription"]["plan"], state["subscription"]["provider"], state["subscription"]["market"]) == ("creator", "infinitepay", "BR")
    # Creator keeps 2 simultaneous lives (approved commercial decision)
    assert state["limits"]["simultaneous"] == 2
    assert sub_state(unpaid)["entitled"] is False


def test_closed_payments_refuse_checkout_through_the_panel(app, client, commercial):
    commercial(False)
    _, c = customer(app)
    for market in ("US", "BR"):
        r = c.post("/api/account/checkout", json={"plan": "basic", "market": market}, headers=CSRF)
        assert r.status_code == 503
        assert r.json()["detail"] == "pagamentos indisponíveis no momento"
    assert sub_state(c)["entitled"] is False
