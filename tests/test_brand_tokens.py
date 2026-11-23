"""The landing (commercial/src/tokens.css) and the subscriber area of the dashboard (dashboard/src/customer-brand.css)
share one brand: every token must exist in both with the same value."""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TOKEN = re.compile(r"(--[a-z0-9-]+)\s*:\s*([^;]+);")


def tokens(path: Path) -> dict[str, str]:
    text = re.sub(r"/\*.*?\*/", "", path.read_text(encoding="utf-8"), flags=re.S)
    return {name: " ".join(value.split()) for name, value in TOKEN.findall(text)}


def test_landing_and_subscriber_area_share_the_same_tokens():
    landing = tokens(ROOT / "commercial" / "src" / "tokens.css")
    account = tokens(ROOT / "dashboard" / "src" / "customer-brand.css")
    assert len(landing) >= 30
    assert landing == account


def test_motion_tokens_are_short_and_reduced_motion_is_handled():
    landing = tokens(ROOT / "commercial" / "src" / "tokens.css")
    durations = {k: int(v.removesuffix("ms")) for k, v in landing.items() if k.startswith("--dur-")}
    assert durations and max(durations.values()) <= 800, durations
    for css in [ROOT / "commercial" / "src" / f for f in ("styles.css", "hero.css")] + [ROOT / "dashboard" / "src" / "account.css"]:
        assert "prefers-reduced-motion: reduce" in css.read_text(encoding="utf-8"), css
