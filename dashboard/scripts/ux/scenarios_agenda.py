"""Real-browser check of the schedule: drag a post to another day and "Reorganizar automaticamente".
LOCAL demo stack only (seed_demo.py). usage: scenarios_agenda.py <base> <admin> <admin_pw> [screenshot_dir]"""

from __future__ import annotations

import sys
from pathlib import Path

from playwright.sync_api import expect, sync_playwright

BASE, ADMIN, ADMIN_PW = sys.argv[1:4]
SHOTS = Path(sys.argv[4]) if len(sys.argv) > 4 else None

with sync_playwright() as p:
    b = p.chromium.launch(channel="chrome")
    page = b.new_page(viewport={"width": 1440, "height": 900}, locale="pt-BR")
    page.goto(BASE + "/login")
    page.get_by_label("E-mail").fill(ADMIN)
    page.get_by_label("Senha").fill(ADMIN_PW)
    page.get_by_role("button", name="Entrar").click()
    page.wait_for_url(lambda u: "/login" not in u)
    page.goto(BASE + "/publicacao")
    expect(page.get_by_role("button", name="Reorganizar automaticamente")).to_be_visible()
    print("ok   botão Reorganizar automaticamente na agenda")

    card = page.locator('.agenda-event[draggable="true"]').first
    expect(card).to_be_visible()
    pid_title = card.locator(".agenda-event-title").inner_text()
    src_day = card.locator("xpath=ancestor::li[contains(@class,'week-day')]")
    days = page.locator("li.week-day")
    idx = [days.nth(i).get_attribute("data-today") for i in range(7)].index("true") if \
        any(days.nth(i).get_attribute("data-today") for i in range(7)) else 0
    target = days.nth(min(6, idx + 1)) if src_day.get_attribute("data-today") else days.nth(6)
    target_day = target.locator(".week-day-head strong").inner_text()
    card.drag_to(target)
    expect(page.get_by_text("Envio movido.")).to_be_visible()
    expect(target.get_by_text(pid_title).first).to_be_visible()
    print(f"ok   arrastar: “{pid_title}” foi para o dia {target_day}")

    page.get_by_role("button", name="Reorganizar automaticamente").click()
    dlg = page.get_by_role("dialog", name="Reorganizar automaticamente")
    expect(dlg).to_be_visible()
    page.wait_for_timeout(1500)
    if SHOTS:
        page.screenshot(path=str(SHOTS / "agenda_autofill.png"))
    apply = dlg.get_by_role("button", name="Aplicar")
    if apply.count():
        label = apply.inner_text()
        apply.click()
        expect(dlg).to_be_hidden()
        print(f"ok   reorganizar automaticamente aplicado ({label})")
    else:
        print("ok   reorganizar automaticamente: nada a fazer")
    if SHOTS:
        page.screenshot(path=str(SHOTS / "agenda_after.png"), full_page=True)
    b.close()
