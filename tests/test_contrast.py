"""The design tokens meet WCAG AA in both themes (spec 9.1)."""

from __future__ import annotations

import importlib.util
from pathlib import Path

_SPEC = importlib.util.spec_from_file_location(
    "check_contrast", Path(__file__).resolve().parent.parent / "scripts" / "check_contrast.py"
)
assert _SPEC and _SPEC.loader
contrast = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(contrast)


def test_known_ratios() -> None:
    assert round(contrast.ratio("#000000", "#ffffff"), 2) == 21.0
    assert round(contrast.ratio("#777777", "#ffffff"), 2) == 4.48


def test_both_themes_are_found() -> None:
    found = contrast.themes(contrast.CSS.read_text(encoding="utf-8"))
    assert set(found) == {"dark", "light"}
    assert found["dark"]["bg"] != found["light"]["bg"]


def test_the_shipped_tokens_pass() -> None:
    assert contrast.check(contrast.CSS.read_text(encoding="utf-8")) == []


def test_a_weak_token_is_reported() -> None:
    css = (
        ":root, [data-theme='dark'] { --bg: #000000; --surface: #000000; "
        "--surface-2: #000000; --text: #ffffff; --muted: #222222; }"
    )
    problems = contrast.check(css)
    assert any("muted on bg" in p for p in problems)
    assert any(p.startswith("light:") for p in problems)
