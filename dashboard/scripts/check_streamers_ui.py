"""Browser check of the Streamers page monitor states (v0.6.2-hotfix.1). The API is simulated INSIDE the browser
(page.route), so nothing touches a real server.

    npx vite --port 5176   (in dashboard/)
    python dashboard/scripts/check_streamers_ui.py http://localhost:5176
"""

from __future__ import annotations

import json
import re
import sys
from datetime import datetime, timedelta, timezone

from playwright.sync_api import expect, sync_playwright

BASE = sys.argv[1]
now = datetime.now(timezone.utc)
iso = lambda d: d.strftime("%Y-%m-%dT%H:%M:%S.%f") + "Z"


def streamer(**kw):
    base = {"id": 1, "platform": "twitch", "channel": "canal", "url": "https://www.twitch.tv/canal", "display_name": "Canal",
            "enabled": True, "status": "live", "stream_title": "", "preferences": {}, "last_checked_at": iso(now - timedelta(hours=9.8)),
            "last_live_at": None, "last_error": "", "created_at": iso(now - timedelta(days=2)), "live_session": None, "clip_count": 0,
            "monitor": {"state": "stale", "check_requested": False, "last_checked_age_s": 35280, "stale_after_s": 300}}
    base.update(kw)
    return base


def ok(name):
    print(f"ok   {name}")


with sync_playwright() as p:
    b = p.chromium.launch()
    pg = b.new_page(viewport={"width": 1280, "height": 800}, locale="pt-BR")
    state = {"list": [streamer()], "check": "refuse", "checks": 0}

    def api(route):
        url = route.request.url
        if url.endswith("/api/auth/me"):
            return route.fulfill(json={"email": "admin@x", "role": "admin"})
        if "/api/streamers/1/check" in url:
            state["checks"] += 1
            if state["check"] == "refuse":
                return route.fulfill(status=503, json={"detail": "API interna indisponível"})
            accepted = iso(datetime.now(timezone.utc))
            state["accepted"] = accepted
            state["list"] = [streamer(monitor={**streamer()["monitor"], "check_requested": True})]
            return route.fulfill(json={"ok": True, "requested_at": accepted, "check_requested": True})
        if re.search(r"/api/streamers(\?.*)?$", url):
            return route.fulfill(json=state["list"])
        if url.endswith("/api/stats"):
            return route.fulfill(json={"clips_by_status": {}, "streamers_enabled": 1, "live_sessions": 0, "pending_jobs": 0, "dead_jobs": 0, "clips_last_24h": []})
        return route.fulfill(json={})

    pg.route("**/api/**", api)
    pg.route("**/healthz", lambda r: r.fulfill(json={"status": "ok", "version": "0.6.2-hotfix.1"}))
    pg.goto(BASE + "/streamers")
    expect(pg.get_by_role("alert").filter(has_text="Monitoramento atrasado")).to_be_visible()
    expect(pg.get_by_text("Não foi possível confirmar se o canal está ao vivo")).to_be_visible()
    expect(pg.get_by_text("o último resultado conhecido era ao vivo")).to_be_visible()
    ok("estado antigo aparece como monitoramento atrasado, com o último resultado separado")

    pg.get_by_role("button", name="Verificar agora").click()
    expect(pg.get_by_text("A solicitação não foi aceita")).to_be_visible()
    expect(pg.get_by_text("Verificação concluída")).to_have_count(0)
    ok("pedido recusado mostra a falha e nunca 'concluída'")

    state["check"] = "accept"
    pg.get_by_role("button", name="Verificar agora").click()
    expect(pg.get_by_text("Verificação solicitada, aguardando o monitor")).to_be_visible()
    pg.wait_for_timeout(3500)  # a poll happens; the monitor still has not reported
    expect(pg.get_by_text("Verificação concluída")).to_have_count(0)
    ok("pedido aceito mas não executado não aparece como concluído")

    # an OLD result arriving (checked before acceptance) must not count either
    state["list"] = [streamer(monitor={**streamer()["monitor"], "check_requested": False})]
    pg.wait_for_timeout(3500)
    expect(pg.get_by_text("Verificação concluída")).to_have_count(0)
    ok("resultado anterior ao pedido não conta como conclusão")

    later = iso(datetime.strptime(state["accepted"], "%Y-%m-%dT%H:%M:%S.%fZ").replace(tzinfo=timezone.utc) + timedelta(seconds=2))
    state["list"] = [streamer(status="offline", last_checked_at=later,
                              monitor={"state": "offline", "check_requested": False, "last_checked_age_s": 1, "stale_after_s": 300})]
    expect(pg.get_by_text("Verificação concluída: offline")).to_be_visible(timeout=8000)
    expect(pg.get_by_role("alert").filter(has_text="Monitoramento atrasado")).to_have_count(0)
    ok("resultado registrado depois do aceite conclui o pedido")
    b.close()
print("todas as verificações da tela de Streamers passaram")
