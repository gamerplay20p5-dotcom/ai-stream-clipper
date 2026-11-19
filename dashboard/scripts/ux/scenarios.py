"""Real-browser UX scenarios for the dashboard (Playwright, Chromium). LOCAL stack only.

Needs: the API on a throwaway SQLite database seeded with seed_demo.py, the dashboard (vite dev or preview) in front
of it, the admin from ASC_ADMIN_EMAIL/ASC_ADMIN_PASSWORD and a guest account with shared clips.

    pip install playwright && python -m playwright install chromium
    python dashboard/scripts/ux/scenarios.py http://localhost:5174 <admin_email> <admin_pw> <guest_email> <guest_pw> <out_dir>

Every check is an assert: the script stops at the first broken scenario. Screenshots go to <out_dir>.
It changes only the local demo data (shares, approvals, a settings draft that is discarded, pause then resume).
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

from playwright.sync_api import Page, expect, sync_playwright

BASE, ADMIN, ADMIN_PW, GUEST, GUEST_PW, OUT = sys.argv[1:7]
OUT_DIR = Path(OUT)
OUT_DIR.mkdir(parents=True, exist_ok=True)
STAFF_API = re.compile(r"/api/(clips|stats|streamers|publish|director|settings|infrastructure|admin|shares|refs)")
PAGES = ["/", "/cortes", "/cortes/1", "/publicacao", "/publicacao?aba=envios", "/diretor", "/ia", "/referencias", "/pessoas"]


def login(page: Page, email: str, pw: str) -> None:
    page.goto(BASE + "/login")
    page.get_by_label("E-mail").fill(email)
    page.get_by_label("Senha").fill(pw)
    page.get_by_role("button", name="Entrar").click()
    page.wait_for_url(lambda u: "/login" not in u)


def ok(name: str) -> None:
    print(f"ok   {name}")


with sync_playwright() as p:
    browser = p.chromium.launch()

    # 1. Guest: only the library, and the browser never calls a staff endpoint.
    ctx = browser.new_context(viewport={"width": 390, "height": 844}, locale="pt-BR")
    guest = ctx.new_page()
    staff_calls: list[str] = []
    guest.on("request", lambda r: STAFF_API.search(r.url) and staff_calls.append(r.url))
    login(guest, GUEST, GUEST_PW)
    expect(guest).to_have_url(re.compile(r"/biblioteca$"))
    expect(guest.get_by_role("heading", name="Minha biblioteca")).to_be_visible()
    for path in ("/cortes", "/publicacao", "/pessoas", "/"):
        guest.goto(BASE + path)
        expect(guest).to_have_url(re.compile(r"/biblioteca$"))
    expect(guest.get_by_role("navigation", name="Navegação principal")).to_have_count(0)
    expect(guest.locator(".library-card").first).to_be_visible()
    guest_cards = guest.locator(".library-card").count()
    assert guest_cards > 0, "the seeded guest should have shared clips"
    guest.locator(".library-card").first.click()
    expect(guest.locator(".library-viewer video")).to_be_visible()
    assert not staff_calls, f"guest browser called staff endpoints: {staff_calls}"
    ok(f"convidado vê só a biblioteca ({guest_cards} vídeos) e não chama rotas da equipe")

    # 2. Admin desktop: navigation, shortcuts help, share and revoke.
    actx = browser.new_context(viewport={"width": 1440, "height": 900}, locale="pt-BR", timezone_id="America/Sao_Paulo")
    admin = actx.new_page()
    errors: list[str] = []
    admin.on("pageerror", lambda e: errors.append(str(e)))
    login(admin, ADMIN, ADMIN_PW)
    nav = admin.get_by_role("navigation", name="Navegação principal")
    for name in ("Hoje", "Central de cortes", "Agenda de publicações", "Diretor IA", "Referências", "Pessoas e acesso"):
        expect(nav.get_by_role("link", name=re.compile(name))).to_be_visible()
    expect(nav.get_by_role("link", name="Servidores")).to_be_hidden()  # system group starts collapsed
    expect(admin.get_by_role("heading", name="Precisa de você")).to_be_visible()
    ok("equipe vê o trabalho do dia primeiro; Sistema começa recolhido")

    admin.goto(BASE + "/cortes/3")
    expect(admin.get_by_role("heading", name="Por que este corte?")).to_be_visible()
    admin.keyboard.press("?")
    expect(admin.get_by_role("dialog", name="Atalhos da Central")).to_be_visible()
    admin.keyboard.press("Escape")
    expect(admin.get_by_role("dialog", name="Atalhos da Central")).to_be_hidden()
    ok("atalhos: ? abre a lista e Esc fecha")

    admin.get_by_role("button", name="Compartilhar", exact=True).click()
    admin.get_by_label("E-mail da conta convidada").fill(GUEST)
    admin.locator(".share-form").get_by_role("button", name="Compartilhar").click()
    expect(admin.get_by_text("Compartilhado. O vídeo já aparece")).to_be_visible()
    guest.goto(BASE + "/biblioteca")
    expect(guest.locator(".library-card")).to_have_count(guest_cards + 1)
    ok("compartilhar pela Central: o vídeo aparece na biblioteca do convidado")

    admin.locator(".share-list .share-row").filter(has_text=GUEST).get_by_role("button", name="Revogar").click()
    expect(admin.locator(".share-list .share-row").filter(has_text=GUEST)).to_have_count(0)
    guest.goto(BASE + "/biblioteca")
    expect(guest.locator(".library-card")).to_have_count(guest_cards)
    ok("revogar: o vídeo some da biblioteca na próxima carga")

    # 3. Agenda: three decision states, detail dialog, pause asks first and can be resumed.
    admin.goto(BASE + "/publicacao")
    grid = admin.get_by_role("list", name=re.compile("Publicações da semana"))
    expect(grid).to_be_visible()
    for label in ("Sugestão", "Confirmado", "Automático"):
        assert grid.locator(".decision-chip", has_text=label).count() + admin.locator(".agenda-legend", has_text=label).count() > 0
    admin.locator(".agenda-event-body").first.click()
    expect(admin.locator(".agenda-dialog")).to_be_visible()
    admin.keyboard.press("Escape")
    admin.get_by_role("button", name="Pausar tudo").click()
    expect(admin.get_by_role("dialog", name="Pausar todas as publicações?")).to_be_visible()
    admin.get_by_role("button", name="Pausar agora").click()
    expect(admin.get_by_text("Publicações pausadas")).to_be_visible()
    admin.goto(BASE + "/")
    expect(admin.get_by_text("As publicações estão pausadas")).to_be_visible()
    admin.goto(BASE + "/publicacao")
    admin.get_by_role("button", name="Retomar envios").click()
    expect(admin.get_by_text("Envios ativos")).to_be_visible()
    ok("agenda: sugestão/confirmado/automático, detalhe, pausa com confirmação e retomada")

    # 4. Settings: essential first, advanced on request, draft counted and discarded.
    admin.goto(BASE + "/ia")
    expect(admin.get_by_role("link", name=re.compile("Modelos de IA"))).to_have_count(0)
    admin.get_by_label("Mostrar ajustes avançados").check()
    expect(admin.get_by_role("link", name=re.compile("Modelos de IA"))).to_be_visible()
    admin.get_by_label("Mostrar ajustes avançados").uncheck()
    field = admin.locator("input[type=number]").first
    field.fill(str(int(field.input_value() or 0) + 1))
    expect(admin.get_by_text("1 alteração não salva")).to_be_visible()
    admin.get_by_role("button", name="Descartar").click()
    expect(admin.get_by_text("1 alteração não salva")).to_have_count(0)
    ok("parâmetros: avançado sob demanda; rascunho contado e descartado")

    # 5. Director in plain language.
    admin.goto(BASE + "/diretor")
    for q in ("O que aconteceu?", "Por que importa?", "O que a IA decidiu?", "Resultado"):
        expect(admin.get_by_role("heading", name=q)).to_be_visible()
    ok("diretor responde às quatro perguntas")
    assert not errors, errors

    # 6. Phone: decision bar reachable, player near the top, no horizontal scroll from 320 px.
    for width in (320, 390):
        mctx = browser.new_context(viewport={"width": width, "height": 740}, locale="pt-BR")
        m = mctx.new_page()
        login(m, ADMIN, ADMIN_PW)
        for path in PAGES:
            m.goto(BASE + path)
            m.wait_for_load_state("networkidle")
            sw = m.evaluate("document.documentElement.scrollWidth")
            assert sw <= width, f"{path} at {width}px scrolls sideways ({sw}px)"
        m.goto(BASE + "/cortes/1")
        bar = m.get_by_role("group", name="Decisão sobre o corte")
        expect(bar).to_be_in_viewport()
        m.wait_for_timeout(800)
        top = m.locator("[data-clip-video]").bounding_box()["y"]
        m.screenshot(path=str(OUT_DIR / f"central-{width}.png"))
        print(f"     player começa em {round(top)} px numa tela de 740 px ({width} px de largura)")
        mctx.close()
    ok("celular: sem rolagem lateral em 320 e 390 px; barra de decisão sempre visível")

    # 7. Reduced motion is honoured.
    rctx = browser.new_context(reduced_motion="reduce", viewport={"width": 1280, "height": 720})
    r = rctx.new_page()
    login(r, ADMIN, ADMIN_PW)
    dur = r.evaluate("getComputedStyle(document.querySelector('.workspace-link')).transitionDuration")
    assert dur in ("1e-05s", "0.00001s", "0s"), dur
    ok("prefers-reduced-motion desliga as transições")
    browser.close()
print("todos os cenários passaram")
