"""Local stack for the subscriber area (/conta): the REAL API and built dashboard on a throwaway SQLite database,
customer accounts in real states and a fake commercial service that only answers coupon quotes and checkout.

No payment, no network, no production data. Used for screenshots and browser scenarios (account_scenarios.py).

    python dashboard/scripts/ux/account_stack.py [--port 8794]      # serves until Ctrl+C and prints the accounts

The accounts (password for all: "senha-de-teste-ux-9"):
    sem-plano@exemplo.test  no subscription, checkout available        (plan selection, coupons, checkout)
    creator@exemplo.test    Creator via InfinitePay (BRL), active, usage and two channels
    pro@exemplo.test        Pro via Stripe (USD), active, auto-renewal, payments history
    atrasado@exemplo.test   Creator via Stripe, payment late (grace)
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "backend"))
PASSWORD = "senha-de-teste-ux-9"
BILLING_SECRET = "ux-billing-link-secret-0123456789abcdef"
CATALOG = json.loads((ROOT / "commercial" / "catalog.json").read_text(encoding="utf-8"))
# Coupons the fake commercial service knows: CLIPPER20 = 20% on the first charge, GRATIS7 = 7 free days
COUPONS = {"CLIPPER20": ("percent", 20), "GRATIS7": ("free_days", 7)}


class FakeCommercial(BaseHTTPRequestHandler):
    """Only what the subscriber page needs. Signatures are not checked: it never leaves 127.0.0.1."""

    def do_POST(self):  # noqa: N802
        body = json.loads(self.rfile.read(int(self.headers.get("content-length", 0))) or b"{}")
        if self.path == "/internal/billing/quote":
            plan, market, code = body.get("plan"), body.get("market"), str(body.get("promo_code", "")).upper()
            price = CATALOG["plans"][plan]["monthlyMinor"][market]
            currency = CATALOG["markets"][market]["currency"].lower()
            kind = COUPONS.get(code)
            if not kind:
                return self._json(200, {"ok": False, "reason": "not_found"})
            if kind[0] == "free_days":
                return self._json(200, {"ok": True, "code": code, "list": price, "discount": 0, "final": price, "currency": currency, "freeDays": kind[1]})
            discount = round(price * kind[1] / 100)
            return self._json(200, {"ok": True, "code": code, "list": price, "discount": discount, "final": price - discount, "currency": currency, "freeDays": 0})
        if self.path == "/internal/billing/checkout":
            time.sleep(0.8)  # lets the browser show the loading state
            host = "https://checkout.stripe.com/c/pay/cs_test_ux" if body.get("market") == "US" else "https://checkout.infinitepay.io/hts/ux"
            return self._json(201, {"url": host})
        if self.path == "/internal/billing/apply-promo":
            return self._json(404, {"error": "promo_invalid", "reason": "no_subscription"})
        if self.path == "/internal/billing/portal":
            return self._json(200, {"url": "https://billing.stripe.com/p/session/ux"})
        return self._json(404, {"error": "not_found"})

    def _json(self, status: int, data: dict):
        raw = json.dumps(data).encode()
        self.send_response(status)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def log_message(self, *args):
        pass


def _serve(handler, port=0):
    server = ThreadingHTTPServer(("127.0.0.1", port), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


def start(port: int = 0, *, checkout: bool = True):
    """Starts everything; returns (base_url, stop). ``checkout=False`` simulates payments unavailable."""
    tmp = Path(tempfile.mkdtemp(prefix="asc_ux_account_"))
    fake = _serve(FakeCommercial)
    os.environ.update({
        "ASC_ENV": "test", "ASC_LOG_JSON": "false", "ASC_LOG_LEVEL": "WARNING",
        "ASC_DATABASE_URL": f"sqlite:///{(tmp / 'ux.db').as_posix()}", "ASC_STORAGE_DIR": str(tmp / "storage"),
        "ASC_JWT_SECRET": "ux-jwt-secret-0123456789abcdef-xyz", "ASC_INTERNAL_SECRET": "ux-internal-secret-0123456789",
        "ASC_ADMIN_EMAIL": "admin@exemplo.test", "ASC_ADMIN_PASSWORD": PASSWORD, "ASC_PUBLIC_ORIGIN": "https://clipper.exemplo.test",
        "ASC_DASHBOARD_DIR": str(ROOT / "dashboard" / "dist"),
        "ASC_BILLING_SECRET": BILLING_SECRET if checkout else "",
        "ASC_COMMERCIAL_URL": f"http://127.0.0.1:{fake.server_address[1]}" if checkout else "",
    })
    import uvicorn

    from clipper.api.app import create_app
    from clipper.config import reset_settings_cache

    reset_settings_cache()
    app = create_app(run_maintenance=False)
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.time() + 30
    while not server.started:
        if time.time() > deadline:
            raise RuntimeError("API did not start")
        time.sleep(0.05)
    base = f"http://127.0.0.1:{server.servers[0].sockets[0].getsockname()[1]}"
    _seed(base)

    def stop():
        server.should_exit = True
        thread.join(10)
        fake.shutdown()

    return base, stop


def _seed(base: str) -> None:
    import uuid

    import httpx

    from clipper import catalog, entitlements
    from clipper.db import session_scope
    from clipper.models import ChannelMonitor, Streamer, User
    from clipper.security import hash_password, sha256_bytes, sign_request

    now = int(time.time())
    day = 86_400

    def customer(email: str) -> int:
        with session_scope() as db:
            u = User(email=email, password_hash=hash_password(PASSWORD), role="customer", active=True)
            db.add(u)
            db.flush()
            return u.id

    def snapshot(uid: int, plan: str, *, provider: str, market: str, status: str = "active", payments=(), manage="stripe_portal", renewal=None):
        payload = {
            "event_id": uuid.uuid4().hex, "seq": 1, "provider": provider, "subscription_ref": f"{provider}_{uid}", "account_id": uid,
            "plan": plan, "market": market, "financial_status": "past_due" if status == "grace" else "active", "review": "",
            "access": {"status": status, "until": now + (3 if status == "grace" else 18) * day, "period_start": now - 12 * day,
                       "period_end": now + (0 if status == "grace" else 18) * day, "renews_at": now + 18 * day if provider == "stripe" else None,
                       "cancel_at_period_end": False},
            "limits": catalog.limits_for(plan), "manage": manage, "renewal": renewal, "payments": list(payments),
        }
        raw = json.dumps(payload).encode()
        headers = sign_request(BILLING_SECRET, "commercial", "POST", "/internal/billing/events", sha256_bytes(raw))
        r = httpx.post(base + "/internal/billing/events", content=raw, headers={**headers, "Content-Type": "application/json"})
        assert r.status_code == 200, r.text

    def channels(uid: int, names: list[tuple[str, str]]):
        with session_scope() as db:
            for name, platform in names:
                url = f"https://www.twitch.tv/{name}" if platform == "twitch" else f"https://www.youtube.com/@{name}"
                s = Streamer(platform=platform, channel=name, display_name=name.replace("_", " ").title(), url=url, hts_monitoring=False)
                db.add(s)
                db.flush()
                db.add(ChannelMonitor(user_id=uid, streamer_id=s.id, preset="balanced"))

    def usage(uid: int, live_hours: int):
        with session_scope() as db:
            ent = entitlements.current(db, uid)
            entitlements.record_usage(db, uid, "live_seconds", f"ux-{uid}", ent.period_key, live_hours * 3600)

    customer("sem-plano@exemplo.test")
    if not os.environ.get("ASC_BILLING_SECRET"):
        return
    creator = customer("creator@exemplo.test")
    snapshot(creator, "creator", provider="infinitepay", market="BR", manage="infinitepay_links",
             payments=[{"id": "ip_1", "at": now - 12 * day, "amount": 13990, "currency": "brl", "status": "paid"}])
    channels(creator, [("cidade_neon", "twitch"), ("noite_ao_vivo", "youtube")])
    usage(creator, 37)
    pro = customer("pro@exemplo.test")
    snapshot(pro, "pro", provider="stripe", market="US",
             payments=[{"id": "in_2", "at": now - 12 * day, "amount": 8999, "currency": "usd", "status": "paid"},
                       {"id": "in_1", "at": now - 42 * day, "amount": 8999, "currency": "usd", "status": "paid"}])
    channels(pro, [("avenida_sul", "twitch")])
    usage(pro, 148)
    late = customer("atrasado@exemplo.test")
    snapshot(late, "creator", provider="stripe", market="US", status="grace",
             payments=[{"id": "in_3", "at": now - 2 * day, "amount": 4499, "currency": "usd", "status": "payment_failed"}])


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8794)
    parser.add_argument("--no-checkout", action="store_true")
    args = parser.parse_args()
    base, stop = start(args.port, checkout=not args.no_checkout)
    print(f"{base}  (senha de todas as contas: {PASSWORD})", flush=True)
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        stop()
