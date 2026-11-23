import json
from clipper import billing_link
from clipper.config import get_settings
status, data = billing_link.call("/internal/billing/status", {})
print("  HTTP", status, json.dumps({k: data.get(k) for k in ("ready", "open", "markets", "stripe", "infinitepay", "outbox")}))
s = get_settings()
print("  API: commercial_url =", s.commercial_url, "| segredo com", len(s.billing_secret or ""), "caracteres | configured =", billing_link.configured())
