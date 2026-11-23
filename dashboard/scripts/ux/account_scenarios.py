"""Browser scenarios for the subscriber area (/conta) on the local stack (account_stack.py): real API and dashboard,
throwaway SQLite, fake commercial service. No payment, no network.

    python dashboard/scripts/ux/account_scenarios.py            # payments available
    python dashboard/scripts/ux/account_scenarios.py --closed   # payments unavailable (no billing link)
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

from playwright.sync_api import Page, expect, sync_playwright

sys.path.insert(0, str(Path(__file__).resolve().parent))
from account_stack import PASSWORD, start  # noqa: E402

CLOSED = "--closed" in sys.argv
passed: list[str] = []


def ok(message: str) -> None:
    passed.append(message)
    print(f"ok   {message}")


def login(page: Page, base: str, email: str) -> None:
    page.goto(base + "/login")
    page.get_by_label("E-mail").fill(email)
    page.get_by_label("Senha").fill(PASSWORD)
    page.get_by_role("button", name="Entrar").click()
    page.wait_for_url(lambda u: "/login" not in u)


def no_overflow(page: Page, label: str) -> None:
    overflow = page.evaluate("document.documentElement.scrollWidth - innerWidth")
    assert overflow <= 1, f"{label}: {overflow}px de rolagem horizontal"


base, stop = start(0, checkout=not CLOSED)
try:
    with sync_playwright() as p:
        browser = p.chromium.launch()

        # Public sign-up stays closed: the login page never offers it
        page = browser.new_page(locale="pt-BR")
        page.goto(base + "/login")
        expect(page.get_by_label("E-mail")).to_be_visible()
        expect(page.get_by_role("button", name=re.compile("Criar conta", re.I))).to_have_count(0)
        assert page.request.get(base + "/api/auth/config").json()["signup_enabled"] is False
        ok("cadastro público fechado: a tela de entrada não oferece criar conta")
        page.close()

        ctx = browser.new_context(viewport={"width": 1440, "height": 900}, locale="pt-BR")
        page = ctx.new_page()
        login(page, base, "sem-plano@exemplo.test")

        if CLOSED:
            page.goto(base + "/conta")
            expect(page.get_by_text("Os pagamentos estão indisponíveis no momento")).to_be_visible()
            expect(page.get_by_role("button", name=re.compile("Assinar o"))).to_be_disabled()
            expect(page.locator("#coupon")).to_have_count(0)
            expect(page.locator(".acct-price b").first).to_have_text("R$ 59,90")
            ok("pagamentos indisponíveis: aviso, botão desativado, sem campo de cupom e preços ainda visíveis")
        else:
            # Prices and allowances from commercial/catalog.json, per currency
            page.goto(base + "/conta?plan=pro")
            prices = page.locator(".acct-plan .acct-price b")
            expect(prices).to_have_text(["R$ 59,90", "R$ 139,90", "R$ 279,90"])
            expect(page.locator(".acct-plan-pro")).to_have_attribute("aria-checked", "true")
            expect(page.locator(".acct-method")).to_contain_text("não há renovação automática")
            expect(page.locator(".acct-plan-creator .acct-plan-features")).to_contain_text("Publicação automática em até 2 contas, 3 posts por dia")
            expect(page.locator(".acct-plan-basic .acct-plan-features")).to_contain_text("Sem publicação automática")
            page.get_by_role("button", name="USD").click()
            expect(prices).to_have_text(["US$ 19,99", "US$ 44,99", "US$ 89,99"])
            expect(page.locator(".acct-method")).to_contain_text("renovação automática mensal")
            page.get_by_role("button", name="BRL").click()
            ok("preços do catálogo em BRL e USD, plano pré-selecionado pelo link e forma de pagamento explicada")

            # Selection by click and by keyboard (radio group)
            page.locator(".acct-plan-basic").click()
            expect(page.locator(".acct-plan-basic")).to_have_attribute("aria-checked", "true")
            expect(page.locator(".acct-plan[aria-checked='true']")).to_have_count(1)
            page.keyboard.press("ArrowRight")
            expect(page.locator(".acct-plan-creator")).to_have_attribute("aria-checked", "true")
            expect(page.locator(".acct-plan-creator")).to_be_focused()
            outline = page.locator(".acct-plan-creator").evaluate("e => getComputedStyle(e).outlineStyle")
            assert outline != "none", outline
            expect(page.get_by_role("button", name="Assinar o Creator")).to_be_visible()
            expect(page.locator(".acct-summary-line b")).to_have_text("R$ 139,90")
            ok("seleção por clique e por setas, foco visível e resumo da cobrança acompanhando o plano")

            # Coupons: invalid, percentage and free days (prices always from the server)
            page.locator("#coupon").fill("NAOEXISTE")
            page.get_by_role("button", name="Aplicar cupom").click()
            expect(page.get_by_role("alert").filter(has_text="cupom não encontrado")).to_be_visible()
            page.locator("#coupon").fill("CLIPPER20")
            page.get_by_role("button", name="Aplicar cupom").click()
            expect(page.get_by_role("status").filter(has_text="Cupom CLIPPER20 aplicado")).to_be_visible()
            expect(page.locator(".acct-plan-creator .acct-price s")).to_have_text("R$ 139,90")
            expect(page.locator(".acct-plan-creator .acct-price b")).to_have_text("R$ 111,92")
            expect(page.locator(".acct-summary-line")).to_contain_text("na primeira cobrança, depois R$ 139,90 por mês")
            page.locator("#coupon").fill("GRATIS7")
            expect(page.locator(".acct-plan-creator .acct-price s")).to_have_count(0)
            page.get_by_role("button", name="Aplicar cupom").click()
            expect(page.locator(".acct-summary-line b")).to_have_text("7 dias grátis")
            ok("cupons: inválido explicado, desconto com preço riscado e renovação ao preço cheio, dias grátis")

            # Checkout: loading state, then the provider page (intercepted: nothing leaves the machine)
            page.route("https://checkout.infinitepay.io/**", lambda route: route.fulfill(status=200, body="<title>InfinitePay (simulado)</title>", content_type="text/html"))
            page.locator("#coupon").fill("")
            page.get_by_role("button", name="Assinar o Creator").click()
            expect(page.get_by_role("button", name="Abrindo o pagamento…")).to_be_disabled()
            page.wait_for_url(re.compile(r"checkout\.infinitepay\.io"))
            ok("checkout: estado de carregamento e redirecionamento para a InfinitePay (interceptado)")

            # Running subscriptions
            for email, plan, facts, chip in [
                ("creator@exemplo.test", "Creator", ["Pix ou cartão, pela InfinitePay", "Manual, por link mensal"], "Ativa"),
                ("pro@exemplo.test", "Pro", ["Cartão, pelo Stripe", "Automática"], "Ativa"),
                ("atrasado@exemplo.test", "Creator", ["Cartão, pelo Stripe"], "Pagamento atrasado"),
            ]:
                c = browser.new_context(viewport={"width": 1440, "height": 900}, locale="pt-BR")
                pg = c.new_page()
                login(pg, base, email)
                pg.goto(base + "/conta")
                expect(pg.locator(".acct-plan-title")).to_have_text(plan)
                expect(pg.locator(".acct-status")).to_have_text(chip)
                for fact in facts:
                    expect(pg.locator(".acct-facts")).to_contain_text(fact)
                if email.startswith("creator"):
                    expect(pg.locator(".acct-meter").first).to_contain_text("37")
                    expect(pg.locator(".acct-meter").first).to_contain_text("de 100 h")
                if email.startswith("pro"):
                    expect(pg.get_by_role("button", name="Abrir o portal do Stripe")).to_be_visible()
                    expect(pg.locator(".acct-table")).to_contain_text("US$ 89,99")
                c.close()
            ok("assinaturas ativas: plano, forma de pagamento, renovação, consumo real, portal e histórico")

        # Mobile and reduced motion
        for width in (390, 768, 1920):
            page.set_viewport_size({"width": width, "height": 900})
            page.goto(base + "/conta")
            page.wait_for_load_state("networkidle")
            no_overflow(page, f"{width}px")
        expect(page.get_by_role("link", name="Meus cortes")).to_be_visible()
        ok("sem rolagem horizontal em 390, 768 e 1920 px; navegação com nomes acessíveis")
        ctx.close()
        rm = browser.new_context(viewport={"width": 390, "height": 844}, locale="pt-BR", reduced_motion="reduce")
        pg = rm.new_page()
        login(pg, base, "sem-plano@exemplo.test")
        pg.goto(base + "/conta")
        expect(pg.locator(".acct-plans")).to_be_visible()
        assert pg.locator(".acct-plans").evaluate("e => getComputedStyle(e).animationName") == "none"
        no_overflow(pg, "390px com movimento reduzido")
        ok("movimento reduzido: sem animação de entrada")
        rm.close()
        browser.close()
    print(f"\n{len(passed)} cenários ok ({'pagamentos fechados' if CLOSED else 'pagamentos disponíveis'})")
finally:
    stop()
