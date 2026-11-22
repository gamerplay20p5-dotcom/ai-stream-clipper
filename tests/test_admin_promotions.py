"""Promotion codes through the main API: admin-only panel routes and the customer's code at checkout.

The commercial service owns the codes and every amount (commercial/server/promotions.test.js covers it with Stripe and
InfinitePay simulated). Here the signed link is simulated: the API must forward only validated input, never compute a
price, never trust the browser for the account, audit every change and say clearly when payments are off.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from conftest import CSRF
from test_billing_entitlements import billing_env, customer  # noqa: F401


def admin_client(app):
    a = TestClient(app)
    assert a.post("/api/auth/login", json={"email": "admin@example.com", "password": "correct-horse-battery"}).status_code == 200
    return a


class Link:
    """Records signed calls and answers like the commercial service."""

    def __init__(self, answers=None):
        self.calls = []
        self.answers = answers or {}

    def __call__(self, path, body):
        self.calls.append((path, body))
        answer = self.answers.get(path, (200, {}))
        return answer(body) if callable(answer) else answer


DEFINITION = {"code": "malaio20", "kind": "percent", "percent": 20, "plans": ["creator", "pro"], "markets": ["BR", "US"]}


def test_panel_routes_are_admin_only_csrf_validated_and_audited(app, client, monkeypatch):
    from clipper import billing_link
    from clipper.db import session_scope
    from clipper.models import AuditLog

    link = Link({"/internal/promotions/create": (201, {"code": "MALAIO20", "stripe": {"state": "synced"}})})
    monkeypatch.setattr(billing_link, "call", link)
    uid, c = customer(app)
    assert c.post("/api/admin/promotions", json=DEFINITION, headers=CSRF).status_code == 403
    a = admin_client(app)
    assert a.post("/api/admin/promotions", json=DEFINITION).status_code == 403, "CSRF header required"
    for bad in ({**DEFINITION, "percent": 0}, {**DEFINITION, "percent": 150}, {**DEFINITION, "plans": ["gold"]},
                {**DEFINITION, "markets": ["EU"]}, {**DEFINITION, "code": "a b"}, {**DEFINITION, "amount_brl": -100},
                {**DEFINITION, "max_per_account": 0}, {**DEFINITION, "applies_to": "everyone"}):
        assert a.post("/api/admin/promotions", json=bad, headers=CSRF).status_code == 422, bad
    assert not link.calls, "invalid input never reaches the commercial service"
    r = a.post("/api/admin/promotions", json={**DEFINITION, "max_redemptions": 100}, headers=CSRF)
    assert r.status_code == 201, r.text
    path, body = link.calls[0]
    assert path == "/internal/promotions/create" and body["actor"] == "admin@example.com"
    assert body["definition"]["code"] == "MALAIO20" and body["definition"]["applies_to"] == "new"
    with session_scope() as db:
        assert db.query(AuditLog).filter(AuditLog.action == "admin.promotion_create", AuditLog.target == "promo:MALAIO20").count() == 1


def test_errors_from_the_commercial_service_are_explained(app, client, monkeypatch):
    from clipper import billing_link

    monkeypatch.setattr(billing_link, "call", Link({"/internal/promotions/create": (422, {"error": "invalid_promotion", "reason": "code_taken"}),
                                                    "/internal/promotions/update": (404, {"error": "invalid_promotion", "reason": "not_found"})}))
    a = admin_client(app)
    r = a.post("/api/admin/promotions", json=DEFINITION, headers=CSRF)
    assert r.status_code == 422 and r.json()["detail"] == "este código já existe"
    assert a.patch("/api/admin/promotions/NADA", json={"active": False}, headers=CSRF).status_code == 404


def test_without_the_commercial_service_codes_are_never_shown_as_working(app, client, monkeypatch):
    from clipper.config import reset_settings_cache

    monkeypatch.delenv("ASC_BILLING_SECRET")
    reset_settings_cache()
    a = admin_client(app)
    assert a.get("/api/admin/promotions").json() == {"connected": False, "codes": [], "stripe": False, "infinitepay": False}
    r = a.post("/api/admin/promotions", json=DEFINITION, headers=CSRF)
    assert r.status_code == 503 and "não conectado" in r.json()["detail"]


def test_details_show_customer_emails_and_changes_are_forwarded(app, client, monkeypatch):
    from clipper import billing_link

    uid, _ = customer(app, "fan-promo@example.com")
    link = Link({"/internal/promotions/details": (200, {"code": "MALAIO20", "redemptions": [{"account_id": uid, "status": "redeemed"}]}),
                 "/internal/promotions/update": (200, {"code": "MALAIO20", "active": False})})
    monkeypatch.setattr(billing_link, "call", link)
    a = admin_client(app)
    d = a.get("/api/admin/promotions/malaio20").json()
    assert d["redemptions"][0]["email"] == "fan-promo@example.com"
    a.patch("/api/admin/promotions/malaio20", json={"active": False, "clear_ends_at": True, "max_per_account": 2}, headers=CSRF)
    assert link.calls[-1] == ("/internal/promotions/update", {"changes": {"code": "MALAIO20", "active": False, "ends_at": None,
                                                                          "max_per_account": 2}, "actor": "admin@example.com"})


def test_customer_checkout_with_a_code(app, client, monkeypatch):
    from clipper import billing_link

    uid, c = customer(app)
    link = Link({"/internal/billing/checkout": lambda body: (201, {"free": True}) if body.get("promo_code") == "GRATIS"
                 else (422, {"error": "promo_invalid", "reason": "account_limit"}) if body.get("promo_code") == "USADO"
                 else (201, {"url": "https://checkout.infinitepay.io/hts/asc-1"})})
    monkeypatch.setattr(billing_link, "call", link)
    r = c.post("/api/account/checkout", json={"plan": "creator", "market": "BR", "promo_code": "malaio20"}, headers=CSRF)
    assert r.status_code == 201 and r.json()["url"].startswith("https://checkout.infinitepay.io/")
    assert link.calls[-1][1]["account_id"] == uid and link.calls[-1][1]["promo_code"] == "MALAIO20"
    assert "price" not in link.calls[-1][1] and "amount" not in link.calls[-1][1], "the browser never sends a price"
    assert c.post("/api/account/checkout", json={"plan": "creator", "market": "BR", "promo_code": "GRATIS"}, headers=CSRF).json() == {"free": True}
    r = c.post("/api/account/checkout", json={"plan": "creator", "market": "BR", "promo_code": "USADO"}, headers=CSRF)
    assert r.status_code == 422 and r.json()["detail"] == "você já usou este cupom"
    assert c.post("/api/account/checkout", json={"plan": "creator", "market": "BR", "promo_code": "x;drop"}, headers=CSRF).status_code == 422


def test_quote_and_recurring_code_on_a_running_subscription(app, client, monkeypatch):
    from clipper import billing_link
    from clipper.db import session_scope
    from clipper.models import AuditLog

    uid, c = customer(app)
    link = Link({"/internal/billing/quote": (200, {"ok": True, "code": "MALAIO20", "list": 13990, "discount": 2798, "final": 11192,
                                                   "currency": "brl", "stripePromotion": "promo_secret"}),
                 "/internal/billing/apply-promo": lambda body: (200, {"ok": True, "code": "FIEL10", "final": 12591, "applied": not body["preview"]})})
    monkeypatch.setattr(billing_link, "call", link)
    q = c.post("/api/account/promo-quote", json={"plan": "creator", "market": "BR", "promo_code": "malaio20"}, headers=CSRF).json()
    assert q["final"] == 11192 and "stripePromotion" not in q
    assert c.post("/api/account/promo-apply", json={"promo_code": "FIEL10", "preview": True}, headers=CSRF).json()["applied"] is False
    assert c.post("/api/account/promo-apply", json={"promo_code": "FIEL10"}, headers=CSRF).json()["applied"] is True
    assert link.calls[-1][1] == {"account_id": uid, "promo_code": "FIEL10", "preview": False}
    with session_scope() as db:
        assert db.query(AuditLog).filter(AuditLog.action == "billing.promo_applied", AuditLog.target == f"user:{uid}").count() == 1
    a = admin_client(app)
    assert a.post("/api/account/promo-apply", json={"promo_code": "FIEL10"}, headers=CSRF).status_code == 403, "customers only"
