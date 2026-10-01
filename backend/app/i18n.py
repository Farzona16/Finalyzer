"""Backend translations: error titles and messages in Uzbek (default) and English.

The catalogs are ``locales/uz.json`` and ``locales/en.json``. They must have identical keys
and identical ``{placeholders}``; a test enforces it (spec section 5.1).
"""

from __future__ import annotations

import json
from functools import cache
from pathlib import Path

LOCALES_DIR = Path(__file__).parent / "locales"
SUPPORTED = ("uz", "en")
DEFAULT_LOCALE = "uz"


@cache
def catalog(locale: str) -> dict[str, str]:
    """Load one locale file; an unsupported locale gets the default catalog."""
    if locale not in SUPPORTED:
        locale = DEFAULT_LOCALE
    path = LOCALES_DIR / f"{locale}.json"
    return json.loads(path.read_text(encoding="utf-8"))


def translate(locale: str, key: str, **params: object) -> str:
    """Return the text for ``key``; fall back to the default locale, then to the key itself."""
    text = catalog(locale).get(key) or catalog(DEFAULT_LOCALE).get(key) or key
    try:
        return text.format(**params)
    except (KeyError, IndexError):
        return text


def parse_accept_language(header: str | None) -> str:
    """Pick the best supported language from an ``Accept-Language`` header.

    Unsupported or malformed input gives the default (Uzbek), as the spec requires.
    """
    if not header:
        return DEFAULT_LOCALE
    ranked: list[tuple[float, int, str]] = []
    for position, part in enumerate(header.split(",")):
        tag, _, rest = part.strip().partition(";")
        quality = 1.0
        if rest.strip().startswith("q="):
            try:
                quality = float(rest.strip()[2:])
            except ValueError:
                continue
        language = tag.strip().lower().split("-")[0]
        if language in SUPPORTED and quality > 0:
            ranked.append((-quality, position, language))
    return min(ranked)[2] if ranked else DEFAULT_LOCALE
