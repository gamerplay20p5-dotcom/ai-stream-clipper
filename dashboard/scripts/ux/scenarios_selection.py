"""Real-browser check: select all / shift-click in the clip queue and access by streamer including clips in review.
LOCAL demo stack only. usage: scenarios_selection.py <base> <admin> <admin_pw> <guest> <guest_pw> [shots]"""

from __future__ import annotations

import sys
from pathlib import Path

from playwright.sync_api import expect, sync_playwright

BASE, ADMIN, ADMIN_PW, GUEST, GUEST_PW = sys.argv[1:6]
SHOTS = Path(sys.argv[6]) if len(sys.argv) > 6 else None


def login(page, email, pw):
    page.goto(BASE + "/login")
    page.get_by_label("E-mail").fill(email)
    page.get_by_label("Senha").fill(pw)
    page.get_by_role("button", name="Entrar").click()
    page.wait_for_url(lambda u: "/login" not in u)


with sync_playwright() as p:
    b = p.chromium.launch(channel="chrome")
    admin = b.new_page(viewport={"width": 1440, "height": 900}, locale="pt-BR")
    login(admin, ADMIN, ADMIN_PW)
    admin.goto(BASE + "/cortes?status=ready")
    boxes = admin.locator(".bin-check input")
    expect(boxes.first).to_be_attached(timeout=15000)
    n = boxes.count()
    assert n >= 3, n
    boxes.nth(0).click()
    boxes.nth(2).click(modifiers=["Shift"])
    expect(admin.get_by_role("button", name="Aprovar 3")).to_be_visible()
    print("ok   Shift+clique marcou 3 cortes de uma vez")
    admin.get_by_label("Selecionar todos", exact=True).click()
    expect(admin.get_by_role("button", name=f"Aprovar {n}")).to_be_visible()
    if SHOTS:
        admin.screenshot(path=str(SHOTS / "sel_all.png"))
    print(f"ok   Selecionar todos marcou os {n} da fila")
    admin.get_by_label("Selecionar todos", exact=True).click()
    expect(admin.get_by_role("button", name=f"Aprovar {n}")).to_be_hidden()
    print("ok   desmarcar todos limpa a seleção")

    users = admin.request.get(BASE + "/api/admin/users").json()
    uid = next(u["id"] for u in users if u["email"] == GUEST)
    st = admin.request.get(BASE + "/api/streamers").json()
    lua = next(s["id"] for s in st if s["display_name"] == "Lua Vilela")
    for g in admin.request.get(BASE + f"/api/streamer-grants?user_id={uid}").json():
        admin.request.delete(BASE + f"/api/streamer-grants/{g['id']}", headers={"X-ASC-CSRF": "1"})
    admin.goto(BASE + "/pessoas")
    admin.get_by_role("button", name=GUEST).click()
    admin.get_by_label("Streamer").select_option(str(lua))
    expect(admin.get_by_label("Incluir os cortes ainda não aprovados")).to_be_checked()
    admin.get_by_role("button", name="Dar acesso ao streamer").click()
    admin.get_by_role("button", name="Dar acesso", exact=True).click()
    expect(admin.get_by_text("Acesso dado:")).to_be_visible()
    print("ok   acesso por streamer com cortes em revisão")

    guest = b.new_page(viewport={"width": 390, "height": 844}, locale="pt-BR")
    login(guest, GUEST, GUEST_PW)
    guest.goto(BASE + "/biblioteca")
    expect(guest.get_by_text("Em revisão").first).to_be_visible()
    if SHOTS:
        guest.screenshot(path=str(SHOTS / "sel_guest.png"), full_page=True)
    print("ok   convidado vê cortes em revisão com o selo")
    b.close()
