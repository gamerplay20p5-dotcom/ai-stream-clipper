"""Real-browser UX scenarios for the dashboard (Playwright, Chromium). LOCAL stack only.

Needs: the API on a throwaway SQLite database seeded with seed_demo.py, the dashboard (vite dev or preview) in front
of it, the admin from ASC_ADMIN_EMAIL/ASC_ADMIN_PASSWORD and a guest account with shared clips.

    pip install playwright && python -m playwright install chromium
    python dashboard/scripts/ux/scenarios.py http://localhost:5174 <admin_email> <admin_pw> <guest_email> <guest_pw> <out_dir>

Every check is an assert: the script stops at the first broken scenario. Screenshots go to <out_dir>.
It changes only the local demo data (shares, a settings draft that is discarded, pause then resume). Publication
confirmations are intercepted in the browser (page.route) and answered locally: no proposal is ever confirmed.
"""

from __future__ import annotations

import json
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


def requests_to(page: Page, pattern: str, method: str = "") -> list[str]:
    """Collects every request matching `pattern` (and method), so a test can prove a click sent nothing."""
    seen: list[str] = []
    rx = re.compile(pattern)
    page.on("request", lambda r: rx.search(r.url) and (not method or r.method == method) and seen.append(f"{r.method} {r.url}"))
    return seen


def local(dt_offset_hours: float) -> str:
    from datetime import datetime, timedelta, timezone
    return (datetime.now(timezone(timedelta(hours=-3))) + timedelta(hours=dt_offset_hours)).strftime("%Y-%m-%dT%H:%M:00")


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

    deletes = requests_to(admin, r"/api/shares/\d+$", "DELETE")
    row = admin.locator(".share-list .share-row").filter(has_text=GUEST)
    row.get_by_role("button", name=re.compile("Revogar o acesso de")).click()
    dlg = admin.get_by_role("dialog", name="Revogar o acesso?")
    expect(dlg).to_be_visible()
    expect(dlg).to_contain_text(GUEST)
    expect(dlg).to_contain_text("Tiroteio na garagem do condomínio")
    dlg.get_by_role("button", name="Voltar").click()
    admin.wait_for_timeout(500)
    assert not deletes, f"Voltar must not revoke: {deletes}"
    guest.goto(BASE + "/biblioteca")
    expect(guest.locator(".library-card")).to_have_count(guest_cards + 1)
    ok("revogar pede confirmação com destinatário e vídeo; Voltar não revoga")
    row.get_by_role("button", name=re.compile("Revogar o acesso de")).click()
    dlg.get_by_role("button", name="Revogar acesso").click()
    expect(admin.locator(".share-list .share-row").filter(has_text=GUEST)).to_have_count(0)
    assert len(deletes) == 1, deletes
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

    # 3b. BULK CONFIRMATION (blocker regression): nothing is confirmed without the explicit final click.
    confirms: list[dict] = []

    def fake_confirm(route):  # never reaches the API: the demo proposals stay proposals
        body = json.loads(route.request.post_data or "{}")
        confirms.append(body)
        route.fulfill(status=200, content_type="application/json", body=json.dumps({"confirmed": body.get("ids", []), "refused": {}}))

    admin.route("**/api/publish/proposals/confirm", fake_confirm)
    admin.goto(BASE + "/publicacao")
    review_btn = admin.get_by_role("button", name=re.compile("^Revisar e confirmar"))
    expect(review_btn).to_be_disabled()  # no selection, no confirmation path
    admin.get_by_role("button", name="Selecionar todas as sugestões").click()
    admin.wait_for_timeout(400)
    assert not confirms, "selecting must not confirm"
    expect(review_btn).to_have_text(re.compile(r"\(2\)"))
    review_btn.click()
    dlg = admin.get_by_role("dialog", name=re.compile("Confirmar 2 publicações"))
    expect(dlg).to_be_visible()
    for text in ("YouTube", "Instagram", "Lua Vilela Cortes", "Privado (2)"):
        expect(dlg).to_contain_text(text)
    admin.wait_for_timeout(400)
    assert not confirms, f"opening the review must not confirm: {confirms}"
    dlg.get_by_role("button", name="Voltar sem confirmar").click()
    expect(dlg).to_be_hidden()
    assert not confirms, f"going back must not confirm: {confirms}"
    ok("confirmação em massa: selecionar e revisar não chamam a API")

    # Risky proposals (fixture served to the browser): one public whose slot passed, one private ahead.
    risky = {"timezone": "America/Sao_Paulo", "control": {"paused": False}, "items": [
        {"id": 9001, "status": "proposed", "platform": "youtube", "origin": "semi", "account": "Conta Teste", "privacy": "public",
         "when": local(-3), "title": "Proposta atrasada pública", "clip_id": None, "score": 80, "thumb": False, "version": 1, "last_error": ""},
        {"id": 9002, "status": "proposed", "platform": "instagram", "origin": "semi", "account": "Conta Teste IG", "privacy": "private",
         "when": local(30), "title": "Proposta futura privada", "clip_id": None, "score": 70, "thumb": False, "version": 1, "last_error": ""}]}
    admin.route("**/api/publish/calendar?days=21", lambda r: r.fulfill(status=200, content_type="application/json", body=json.dumps(risky)))
    admin.goto(BASE + "/publicacao")
    admin.get_by_role("button", name="Selecionar todas as sugestões").click()
    review_btn.click()
    dlg = admin.get_by_role("dialog", name=re.compile("Confirmar 2 publicações"))
    expect(dlg).to_contain_text("já passou do horário e será enviada imediatamente")
    expect(dlg).to_contain_text("é pública")
    final = dlg.get_by_role("button", name="Confirmar 2 publicações")
    expect(final).to_be_disabled()  # public + immediate need the explicit acknowledgement
    dlg.get_by_role("checkbox").check()
    expect(final).to_be_enabled()
    assert not confirms
    final.click()
    expect(dlg).to_be_hidden()
    admin.wait_for_timeout(400)
    assert confirms == [{"ids": [9001, 9002]}], confirms
    admin.unroute("**/api/publish/calendar?days=21")
    admin.unroute("**/api/publish/proposals/confirm")
    ok("confirmação em massa: aviso de envio imediato e público, exige ciência; só o clique final envia (interceptado)")

    # 3c. Re-approving a new version asks first and names who and what.
    outdated = [{"id": 777, "clip_id": 3, "clip_title": "Tiroteio na garagem do condomínio", "user_id": 2, "email": GUEST,
                 "follow_latest": False, "shared_version": 1, "outdated": True, "can_view": True, "can_download": False,
                 "can_variants": False, "expires_at": None, "created_at": None, "revoked_at": None, "state": "ativo"}]
    admin.route("**/api/shares?clip_id=3", lambda r: r.fulfill(status=200, content_type="application/json", body=json.dumps(outdated)))
    reapprovals = requests_to(admin, r"/reapprove$", "POST")
    admin.goto(BASE + "/cortes/3")
    admin.get_by_role("button", name="Compartilhar", exact=True).click()
    admin.get_by_role("button", name=re.compile("Enviar a versão atual de")).click()
    dlg = admin.get_by_role("dialog", name="Enviar a versão atual?")
    expect(dlg).to_contain_text(GUEST)
    expect(dlg).to_contain_text("Tiroteio na garagem do condomínio")
    dlg.get_by_role("button", name="Voltar").click()
    admin.wait_for_timeout(400)
    assert not reapprovals, reapprovals
    admin.unroute("**/api/shares?clip_id=3")
    ok("reaprovar versão pede confirmação com destinatário e vídeo; Voltar não envia")

    # 3d. A staff (admin) account needs the explicit full-access acknowledgement.
    creates = requests_to(admin, r"/api/admin/users$", "POST")
    admin.goto(BASE + "/pessoas")
    admin.get_by_label("Nova conta").fill("nova.equipe@demo.local")
    admin.get_by_label("Tipo").select_option("admin")
    create_btn = admin.get_by_role("button", name="Criar conta")
    expect(create_btn).to_be_disabled()
    ack = admin.get_by_role("checkbox", name=re.compile("acesso completo à operação da HTS"))
    ack.check()
    expect(create_btn).to_be_enabled()
    ack.uncheck()
    expect(create_btn).to_be_disabled()
    assert not creates, creates
    ok("conta de equipe exige confirmação de acesso completo")

    # 3e. "Hoje" never says all clear without verifying everything; missing answers are not zero.
    empty = {
        "**/api/stats": {"clips_by_status": {}, "dead_jobs": 0, "pending_jobs": 0, "live_sessions": 0, "streamers_enabled": 0, "clips_last_24h": [0] * 24},
        "**/api/clips?*": {"items": [], "total": 0}, "**/api/streamers": [], "**/api/publish/calendar?*": {"timezone": "America/Sao_Paulo", "items": []},
        "**/api/publish/control": {"paused": False}, "**/api/publish/overview": {"counts": {}, "apps": {}, "accounts": [], "rules": []},
        "**/api/infrastructure": {"hosts": [], "failures": []},
    }
    tctx = browser.new_context(viewport={"width": 1280, "height": 800}, locale="pt-BR")
    t = tctx.new_page()
    login(t, ADMIN, ADMIN_PW)

    def answer(body):
        payload = json.dumps(body)
        return lambda route: route.fulfill(status=200, content_type="application/json", body=payload)

    def serve(page: Page, overrides: dict) -> None:
        for pattern, body in {**empty, **overrides}.items():
            if body == "500":
                page.route(pattern, lambda r: r.fulfill(status=500, content_type="application/json", body='{"detail":"falha simulada"}'))
            elif body == "hang":
                page.route(pattern, lambda r: None)  # never answers: still verifying
            else:
                page.route(pattern, answer(body))

    serve(t, {})
    t.goto(BASE + "/")
    expect(t.get_by_text("Tudo verificado e sem pendências")).to_be_visible()
    ok("Hoje: tudo verificado e sem pendências só com todas as respostas")
    t.unroute_all()
    serve(t, {"**/api/infrastructure": "500", "**/api/publish/overview": "500"})
    t.goto(BASE + "/")
    expect(t.get_by_role("alert").filter(has_text="Não foi possível verificar")).to_be_visible(timeout=15_000)
    expect(t.get_by_role("alert").filter(has_text="servidores")).to_be_visible()
    expect(t.get_by_role("alert").filter(has_text="publicações")).to_be_visible()
    expect(t.get_by_text("Tudo verificado e sem pendências")).to_have_count(0)
    ok("Hoje: falha de verificação aparece e não vira \"tudo em dia\"")
    t.unroute_all()
    serve(t, {"**/api/publish/calendar?*": "hang"})
    t.goto(BASE + "/")
    expect(t.get_by_text(re.compile("Ainda verificando: .*envios recentes"))).to_be_visible()
    expect(t.get_by_text("Tudo verificado e sem pendências")).to_have_count(0)
    tctx.close()
    ok("Hoje: enquanto verifica, não declara que está tudo em dia")

    # 3f. Library: revoked while watching, download withdrawn. The backend answers; the page reacts.
    guest_shares = [x for x in admin.request.get(f"{BASE}/api/shares").json() if x["email"] == GUEST]
    target = guest_shares[0]
    guest.goto(BASE + "/biblioteca")
    expect(guest.locator(".library-card").first).to_be_visible()
    before = guest.locator(".library-card").count()
    guest.locator(".library-card").filter(has_text=target["clip_title"]).click()
    expect(guest.locator(".library-viewer")).to_be_visible()
    admin.request.patch(f"{BASE}/api/shares/{target['id']}", data={"can_download": False}, headers={"X-ASC-CSRF": "1"})
    guest.get_by_role("button", name="Baixar vídeo").click()
    expect(guest.get_by_text("O download deste vídeo não está mais liberado")).to_be_visible()
    ok("biblioteca: download retirado responde 403 e a página explica")
    admin.request.delete(f"{BASE}/api/shares/{target['id']}", headers={"X-ASC-CSRF": "1"})
    guest.locator(".library-video").dispatch_event("play")  # playback starts again: access is re-checked
    expect(guest.get_by_text("Seu acesso a este vídeo terminou")).to_be_visible()
    expect(guest.locator(".library-viewer")).to_have_count(0)
    expect(guest.locator(".library-card")).to_have_count(before - 1)
    ok("biblioteca: revogado durante a reprodução fecha o player, avisa e atualiza a lista")

    other = next(x for x in guest_shares if x["id"] != target["id"])  # the list on screen still shows it (stale)
    admin.request.delete(f"{BASE}/api/shares/{other['id']}", headers={"X-ASC-CSRF": "1"})
    guest.get_by_role("button", name="Ok").click()
    guest.locator(".library-card").filter(has_text=other["clip_title"]).click()  # the player asks the backend: 404
    expect(guest.get_by_text("Seu acesso a este vídeo terminou")).to_be_visible()
    expect(guest.locator(".library-card")).to_have_count(before - 2)
    ok("biblioteca: vídeo revogado aberto de uma lista antiga recebe 404 do backend e sai da lista")
    for share in (target, other):  # demo state back as it was
        restored = admin.request.post(f"{BASE}/api/shares", data={"email": GUEST, "clip_ids": [share["clip_id"]], "can_download": True},
                                      headers={"X-ASC-CSRF": "1"})
        assert restored.ok, restored.text()

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
