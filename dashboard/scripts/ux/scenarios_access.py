"""Real-browser scenarios for "give more access" (people page) and the library organised by streamer.
LOCAL demo stack only (seed_demo.py). usage: scenarios_access.py <base> <admin> <admin_pw> <guest> <guest_pw>"""

from __future__ import annotations

import re
import sys

from playwright.sync_api import Page, expect, sync_playwright

BASE, ADMIN, ADMIN_PW, GUEST, GUEST_PW = sys.argv[1:6]
CSRF = {"X-ASC-CSRF": "1"}


def login(page: Page, email: str, pw: str) -> None:
    page.goto(BASE + "/login")
    page.get_by_label("E-mail").fill(email)
    page.get_by_label("Senha").fill(pw)
    page.get_by_role("button", name="Entrar").click()
    page.wait_for_url(lambda u: "/login" not in u)


def ok(msg: str) -> None:
    print(f"ok   {msg}")


with sync_playwright() as p:
    b = p.chromium.launch()
    admin = b.new_page(viewport={"width": 1440, "height": 900}, locale="pt-BR")
    login(admin, ADMIN, ADMIN_PW)
    api = admin.request
    users = api.get(BASE + "/api/admin/users").json()
    guest_id = next(u["id"] for u in users if u["email"] == GUEST)
    streamers = {s["display_name"]: s["id"] for s in api.get(BASE + "/api/streamers").json()}
    lua, theo = streamers["Lua Vilela"], streamers["Theo Brasa"]
    approved_lua = [c["id"] for c in api.get(BASE + f"/api/clips?status=approved&streamer_id={lua}").json()["items"]]

    guest = b.new_page(viewport={"width": 390, "height": 844}, locale="pt-BR")
    login(guest, GUEST, GUEST_PW)
    base_items = {i["clip_id"] for i in guest.request.get(BASE + "/api/library").json()["items"]}

    # 1. access to every approved clip of a streamer, with explicit confirmation
    admin.goto(BASE + "/pessoas")
    admin.get_by_role("button", name=re.compile(re.escape(GUEST))).click()
    box = admin.locator(".give-access")
    expect(box.get_by_role("heading", name="Dar mais acesso")).to_be_visible()
    box.get_by_label("Streamer").select_option(str(lua))
    box.get_by_role("button", name="Dar acesso ao streamer").click()
    dlg = admin.get_by_role("dialog", name=re.compile("Dar acesso aos cortes de Lua Vilela"))
    expect(dlg).to_contain_text(GUEST)
    dlg.get_by_role("button", name="Dar acesso").click()
    expect(box.get_by_text(re.compile("Acesso dado"))).to_be_visible()
    expect(box.get_by_text("Todos os cortes aprovados de Lua Vilela")).to_be_visible()
    lib = {i["clip_id"] for i in guest.request.get(BASE + "/api/library").json()["items"]}
    assert set(approved_lua) <= lib, (approved_lua, lib)
    ok(f"acesso por streamer com confirmação: {len(approved_lua)} cortes aprovados da Lua entram na biblioteca")

    # 2. hand-picked clips of another streamer -> the library groups by streamer
    theo_clip = next(c["id"] for c in api.get(BASE + f"/api/clips?streamer_id={theo}").json()["items"] if c["has_video"])
    api.post(BASE + f"/api/clips/{theo_clip}/approve", headers=CSRF)
    box.get_by_role("tab", name="Escolher cortes").click()
    box.get_by_label("Streamer").select_option(str(theo))
    box.get_by_role("checkbox", name=re.compile("Escolher")).first.check()
    box.get_by_role("button", name=re.compile(r"Compartilhar 1 corte")).click()
    expect(box.get_by_text("1 corte compartilhado")).to_be_visible()
    guest.goto(BASE + "/biblioteca")
    expect(guest.get_by_role("heading", name=re.compile("^Lua Vilela"))).to_be_visible()
    expect(guest.get_by_role("heading", name=re.compile("^Theo Brasa"))).to_be_visible()
    nav = guest.get_by_role("navigation", name="Streamers")
    nav.get_by_role("button", name=re.compile("^Theo Brasa")).click()
    expect(guest.get_by_role("heading", name=re.compile("^Lua Vilela"))).to_have_count(0)
    expect(guest.get_by_role("heading", name=re.compile("^Theo Brasa"))).to_be_visible()
    nav.get_by_role("button", name=re.compile("^Todos")).click()
    sw = guest.evaluate("document.documentElement.scrollWidth")
    assert sw <= 390, sw
    guest.screenshot(path="biblioteca-por-streamer.png", full_page=True)
    ok("escolher cortes de outro streamer; biblioteca agrupada e filtrável por streamer (390 px sem rolagem lateral)")

    # 3. approving later reaches the guest; rejecting withdraws only what came from the grant
    new = next(c["id"] for c in api.get(BASE + f"/api/clips?status=ready&streamer_id={lua}").json()["items"] if c["has_video"])
    api.post(BASE + f"/api/clips/{new}/approve", headers=CSRF)
    assert new in {i["clip_id"] for i in guest.request.get(BASE + "/api/library").json()["items"]}
    api.post(BASE + f"/api/clips/{new}/reject", headers=CSRF)
    assert new not in {i["clip_id"] for i in guest.request.get(BASE + "/api/library").json()["items"]}
    ok("aprovação posterior entra; rejeição tira o corte que veio pelo acesso ao streamer")

    # 4. revoking the grant asks first and keeps explicit shares
    box.get_by_role("tab", name="Todos os cortes de um streamer").click()
    deletes: list[str] = []
    admin.on("request", lambda r: r.method == "DELETE" and "/streamer-grants/" in r.url and deletes.append(r.url))
    box.get_by_role("button", name=re.compile("Revogar o acesso de .* aos cortes de Lua Vilela")).click()
    d2 = admin.get_by_role("dialog", name="Revogar o acesso ao streamer?")
    d2.get_by_role("button", name="Voltar").click()
    admin.wait_for_timeout(300)
    assert not deletes
    box.get_by_role("button", name=re.compile("Revogar o acesso de .* aos cortes de Lua Vilela")).click()
    d2.get_by_role("button", name="Revogar acesso").click()
    expect(box.get_by_text("Todos os cortes aprovados de Lua Vilela")).to_have_count(0)
    after = {i["clip_id"] for i in guest.request.get(BASE + "/api/library").json()["items"]}
    assert base_items <= after and theo_clip in after, (base_items, after)  # explicit shares survive
    ok("revogar o acesso ao streamer pede confirmação e mantém os compartilhamentos manuais")

    # 5. account type change asks first; nothing is sent on "Voltar"
    patches: list[str] = []
    admin.on("request", lambda r: r.method == "PATCH" and "/api/admin/users/" in r.url and patches.append(r.url))
    box.get_by_role("tab", name="Tipo de conta").click()
    box.get_by_role("button", name="Tornar da equipe").click()
    d3 = admin.get_by_role("dialog", name="Tornar esta conta da equipe?")
    expect(d3).to_contain_text("acesso completo à operação da HTS")
    d3.get_by_role("button", name="Voltar").click()
    admin.wait_for_timeout(300)
    assert not patches
    ok("tornar da equipe exige confirmação; Voltar não altera a conta")

    # 6. a deactivated account explains how to proceed
    r = api.post(BASE + "/api/admin/users", data={"email": "desativada@demo.local"}, headers=CSRF)
    uid = r.json()["id"] if r.ok else next(u["id"] for u in api.get(BASE + "/api/admin/users").json() if u["email"] == "desativada@demo.local")
    api.patch(BASE + f"/api/admin/users/{uid}", data={"active": False}, headers=CSRF)
    admin.goto(BASE + "/pessoas")
    admin.get_by_role("button", name=re.compile("desativada@demo.local")).click()
    expect(admin.get_by_text("Reative a conta para dar acesso a mais cortes")).to_be_visible()
    admin.screenshot(path="pessoas-dar-acesso.png", full_page=True)
    ok("conta desativada mostra que é preciso reativar antes")

    api.post(BASE + f"/api/clips/{theo_clip}/reject", headers=CSRF)  # demo data back as it was
    b.close()
print("todos os cenários de acesso passaram")
