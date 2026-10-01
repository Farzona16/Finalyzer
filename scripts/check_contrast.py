"""WCAG AA contrast check for the design tokens in frontend/src/index.css (spec 9.1).

Text and muted text must reach 4.5:1 on bg, surface and surface-2 in both themes.
Accent colours are used for icons, borders and large text, so they need 3:1 on those surfaces.

Run: ``python scripts/check_contrast.py`` (exit code 1 on a failure).
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

CSS = Path(__file__).resolve().parent.parent / "frontend" / "src" / "index.css"
SURFACES = ("bg", "surface", "surface-2")
TEXT = ("text", "muted")
ACCENTS = ("green", "blue", "amber", "teal", "red", "purple")
TEXT_MIN = 4.5
ACCENT_MIN = 3.0
_BLOCK = re.compile(r"(?P<selector>[^{}]+)\{(?P<body>[^{}]*)\}")
_TOKEN = re.compile(r"--([a-z0-9-]+):\s*(#[0-9a-fA-F]{6})\s*;")


def themes(css: str) -> dict[str, dict[str, str]]:
    """Token values per theme (`:root` and the dark selector are dark)."""
    result: dict[str, dict[str, str]] = {}
    for block in _BLOCK.finditer(css):
        selector = block["selector"]
        tokens = dict(_TOKEN.findall(block["body"]))
        if not tokens:
            continue
        if "data-theme='light'" in selector or 'data-theme="light"' in selector:
            result.setdefault("light", {}).update(tokens)
        elif "data-theme='dark'" in selector or 'data-theme="dark"' in selector:
            result.setdefault("dark", {}).update(tokens)
    return result


def _channel(value: int) -> float:
    c = value / 255
    return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4


def luminance(color: str) -> float:
    r, g, b = (int(color[i : i + 2], 16) for i in (1, 3, 5))
    return 0.2126 * _channel(r) + 0.7152 * _channel(g) + 0.0722 * _channel(b)


def ratio(foreground: str, background: str) -> float:
    a, b = luminance(foreground), luminance(background)
    lighter, darker = max(a, b), min(a, b)
    return (lighter + 0.05) / (darker + 0.05)


def check(css: str) -> list[str]:
    """Human-readable failures; an empty list means every pair passes."""
    problems: list[str] = []
    found = themes(css)
    for theme in ("dark", "light"):
        tokens = found.get(theme)
        if not tokens:
            problems.append(f"{theme}: no tokens found")
            continue
        for names, minimum in ((TEXT, TEXT_MIN), (ACCENTS, ACCENT_MIN)):
            for fg in names:
                for bg in SURFACES:
                    if fg not in tokens or bg not in tokens:
                        problems.append(f"{theme}: missing token {fg} or {bg}")
                        continue
                    value = ratio(tokens[fg], tokens[bg])
                    if value < minimum:
                        problems.append(
                            f"{theme}: {fg} on {bg} is {value:.2f}:1, needs {minimum}:1"
                        )
    return problems


def main() -> int:
    problems = check(CSS.read_text(encoding="utf-8"))
    for problem in problems:
        print("FAIL", problem)
    if not problems:
        print("contrast OK (AA) in both themes")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
