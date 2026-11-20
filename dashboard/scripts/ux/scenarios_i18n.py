"""English interface check in a real browser (LOCAL demo stack only, seed_demo.py).

Opens every staff page and the guest library with an en-US browser, lists visible lines that still look
Portuguese (interface leftovers or text that comes from the server) and checks the language picker.
usage: scenarios_i18n.py <base> <admin> <admin_pw> <guest> <guest_pw> [screenshot_dir]
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

from playwright.sync_api import Page, expect, sync_playwright

BASE, ADMIN, ADMIN_PW, GUEST, GUEST_PW = sys.argv[1:6]
SHOTS = Path(sys.argv[6]) if len(sys.argv) > 6 else None
PT = re.compile(r"[ãõçÃÕÇ]|\b(não|você|está|são|também|então|já|até|após|corte|cortes|agora|nenhum|nenhuma|"
                r"para|com|sem|uma|dos|das|pelo|pela|ainda|aqui|hoje|sessão|publicação|ao vivo)\b", re.I)
STAFF_PAGES = ["/", "/cortes", "/publicacao", "/publicacao?aba=contas", "/publicacao?aba=regras", "/diretor",
               "/referencias", "/pessoas", "/streamers", "/ia", "/avaliacao", "/diagnostico", "/infra",
               "/armazenamento", "/patch-notes"]
# demo data typed by people (channel names, clip titles, transcripts) is content, not interface
CONTENT = re.compile(r"Lua Vilela|Theo Brasa|demo\.local|#\d+")


def login(page: Page, email: str, pw: str) -> None:
    page.goto(BASE + "/login")
    page.get_by_label("Email").fill(email)
    page.get_by_label("Password").fill(pw)
    page.get_by_role("button", name="Sign in").click()
    page.wait_for_url(lambda u: "/login" not in u)


def leftovers(page: Page) -> list[str]:
    page.wait_for_load_state("networkidle")
    text = page.locator("body").inner_text()
    out = []
    for line in dict.fromkeys(x.strip() for x in text.splitlines()):
        if line and PT.search(line) and not CONTENT.search(line):
            out.append(line[:140])
    return out


report: dict[str, list[str]] = {}
with sync_playwright() as p:
    b = p.chromium.launch(channel="chrome")
    ctx = b.new_context(viewport={"width": 1440, "height": 900}, locale="en-US")
    page = ctx.new_page()
    page.goto(BASE + "/login")
    expect(page.get_by_role("heading", name="Sign in to the dashboard")).to_be_visible()
    print("ok   login in English for an en-US browser")
    login(page, ADMIN, ADMIN_PW)
    for path in STAFF_PAGES:
        page.goto(BASE + path)
        page.wait_for_timeout(600)
        report[path] = leftovers(page)
        if SHOTS:
            page.screenshot(path=str(SHOTS / f"en{path.replace('/', '_').replace('?', '_') or '_home'}.png"), full_page=True)
    expect(page.get_by_role("link", name="Today")).to_be_visible()
    print("ok   navigation in English")

    # language picker: switch to Portuguese, the choice survives a reload, then back to English
    page.goto(BASE + "/")
    page.get_by_label("Conta e sessão").or_(page.get_by_label("Account and session")).first.click()
    page.get_by_label("Idioma / Language").first.select_option("pt")
    page.wait_for_load_state("load")
    expect(page.get_by_role("link", name="Hoje")).to_be_visible()
    page.reload()
    expect(page.get_by_role("link", name="Hoje")).to_be_visible()
    expect(page.locator("html")).to_have_attribute("lang", "pt-BR")
    print("ok   picker switches to Portuguese and the choice survives a reload")
    page.get_by_label("Conta e sessão").click()
    page.get_by_label("Idioma / Language").first.select_option("en")
    page.wait_for_load_state("load")
    expect(page.get_by_role("link", name="Today")).to_be_visible()
    print("ok   picker switches back to English")

    guest_ctx = b.new_context(viewport={"width": 390, "height": 844}, locale="en-US")
    guest = guest_ctx.new_page()
    login(guest, GUEST, GUEST_PW)
    guest.goto(BASE + "/biblioteca")
    expect(guest.get_by_role("heading", name="My library")).to_be_visible()
    report["/biblioteca (guest)"] = leftovers(guest)
    if SHOTS:
        guest.screenshot(path=str(SHOTS / "en_library_guest.png"), full_page=True)
    print("ok   guest library in English")

    pt_ctx = b.new_context(locale="pt-BR")
    pt = pt_ctx.new_page()
    pt.goto(BASE + "/login")
    expect(pt.get_by_role("heading", name="Acesse o painel")).to_be_visible()
    print("ok   a pt-BR browser still gets Portuguese")
    b.close()

total = 0
for path, lines in report.items():
    for line in lines:
        total += 1
        print(f"PT   {path}: {line}")
print(f"{total} linhas com cara de português em {len(report)} telas")
