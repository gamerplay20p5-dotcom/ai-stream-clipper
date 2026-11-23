"""Screenshots of the subscriber area (/conta) for each account state and width, on the local stack.

    python dashboard/scripts/ux/account_capture.py <output dir>
"""

from __future__ import annotations

import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

sys.path.insert(0, str(Path(__file__).resolve().parent))
from account_stack import PASSWORD, start  # noqa: E402

OUT = Path(sys.argv[1] if len(sys.argv) > 1 else "account-capture")
OUT.mkdir(parents=True, exist_ok=True)
ACCOUNTS = ["sem-plano", "creator", "pro", "atrasado"]
WIDTHS = [(390, 844), (768, 1024), (1440, 900), (1920, 1080)]

base, stop = start(0)
try:
    with sync_playwright() as p:
        browser = p.chromium.launch()
        for account in ACCOUNTS:
            ctx = browser.new_context(viewport={"width": 1440, "height": 900}, locale="pt-BR")
            page = ctx.new_page()
            page.goto(base + "/login")
            page.get_by_label("E-mail").fill(f"{account}@exemplo.test")
            page.get_by_label("Senha").fill(PASSWORD)
            page.get_by_role("button", name="Entrar").click()
            page.wait_for_url(lambda u: "/login" not in u)
            for width, height in WIDTHS:
                page.set_viewport_size({"width": width, "height": height})
                page.goto(base + "/conta?plan=creator")
                page.wait_for_load_state("networkidle")
                page.wait_for_timeout(700)
                page.screenshot(path=str(OUT / f"conta-{account}-{width}-viewport.png"))
                page.screenshot(path=str(OUT / f"conta-{account}-{width}-full.png"), full_page=True)
                overflow = page.evaluate("document.documentElement.scrollWidth - innerWidth")
                if overflow > 1:
                    print(f"OVERFLOW {account} {width}px: {overflow}px")
            ctx.close()
        browser.close()
    print(f"ok: capturas em {OUT}")
finally:
    stop()
