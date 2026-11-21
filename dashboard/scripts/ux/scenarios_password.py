"""Real-browser check of the password flows (LOCAL demo stack only).
usage: scenarios_password.py <base> <admin> <admin_pw> [screenshot_dir]"""

from __future__ import annotations

import sys
import time
from pathlib import Path

from playwright.sync_api import expect, sync_playwright

BASE, ADMIN, ADMIN_PW = sys.argv[1:4]
SHOTS = Path(sys.argv[4]) if len(sys.argv) > 4 else None
NEW = f"senha{int(time.time())}@demo.local"


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
    admin.goto(BASE + "/pessoas")
    admin.get_by_label("Nova conta").fill(NEW)
    admin.get_by_label("Como a pessoa recebe o acesso").select_option("link")
    admin.get_by_role("button", name="Criar conta").click()
    code = admin.locator(".one-time-code")
    expect(code).to_contain_text("/definir-senha?t=")
    url = code.inner_text().strip()
    if SHOTS:
        admin.screenshot(path=str(SHOTS / "pw_admin_link.png"))
    print("ok   conta criada com link de senha")

    guest = b.new_page(viewport={"width": 390, "height": 844}, locale="pt-BR")
    guest.goto(url)
    expect(guest.get_by_role("heading", name="Criar a sua senha")).to_be_visible()
    guest.get_by_label("Nova senha", exact=True).fill("aaaaaaaaaaaa")
    guest.get_by_label("Repita a nova senha").fill("aaaaaaaaaaaa")
    guest.get_by_role("button", name="Salvar senha").click()
    expect(guest.get_by_text("esta senha é fácil demais de adivinhar")).to_be_visible()
    guest.get_by_label("Nova senha", exact=True).fill("pedra-lua-cavalo-7")
    guest.get_by_label("Repita a nova senha").fill("pedra-lua-cavalo-7")
    guest.get_by_role("button", name="Salvar senha").click()
    expect(guest.get_by_text("Senha criada. Use-a para entrar.")).to_be_visible()
    if SHOTS:
        guest.screenshot(path=str(SHOTS / "pw_set.png"))
    print("ok   senha criada pelo link (senha fraca recusada antes)")
    guest.goto(url)
    expect(guest.get_by_text("este link não vale mais")).to_be_visible()
    print("ok   o link não vale uma segunda vez")

    login(guest, NEW, "pedra-lua-cavalo-7")
    guest.goto(BASE + "/conta/senha")
    guest.get_by_label("Senha atual").fill("pedra-lua-cavalo-7")
    guest.get_by_label("Nova senha", exact=True).fill("outra-frase-segura-9")
    guest.get_by_label("Repita a nova senha").fill("outra-frase-segura-9")
    guest.get_by_role("button", name="Trocar senha").click()
    expect(guest.get_by_text("Senha trocada.")).to_be_visible()
    print("ok   convidado trocou a própria senha")

    anon = b.new_page(locale="pt-BR")
    anon.goto(BASE + "/login")
    anon.get_by_role("button", name="Esqueci minha senha").click()
    expect(anon.get_by_text("O envio de e-mail ainda não está configurado")).to_be_visible()
    print("ok   esqueci minha senha sem e-mail configurado orienta a pedir um link")

    admin.goto(BASE + "/infra")
    expect(admin.get_by_role("heading", name="212")).to_be_visible()
    print("ok   máquina 212 aparece em Servidores")
    b.close()
