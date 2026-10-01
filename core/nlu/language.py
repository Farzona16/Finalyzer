"""Detect whether a question is Uzbek, English or Russian (spec 7.1).

Script first: Cyrillic letters mean Russian. Otherwise a small keyword score decides
Uzbek vs English; a tie (or no keywords) is treated as Uzbek, the product default.
"""

from __future__ import annotations

import re

from core.nlu.normalize import match_key, normalize_text

CYRILLIC_RE = re.compile(r"[\u0400-\u04FF]")
UZ_HINTS = (
    "qancha",
    "qaysi",
    "nechta",
    "boyicha",
    "nisbatan",
    "oxirgi",
    "oyda",
    "yil",
    "ulushi",
    "daromad",
    "savdo",
    "bitim",
    "filial",
    "dokon",
    "tranzaksiya",
    "yutish",
    "mijoz",
    "otgan",
    "jami",
    "soni",
    "kategoriya",
    "kanal",
)
EN_HINTS = (
    "what",
    "which",
    "how",
    "total",
    "revenue",
    "average",
    "share",
    "last",
    "month",
    "year",
    "compared",
    "show",
    "many",
    "much",
    "failed",
    "branch",
    "store",
    "trend",
    "over",
    "time",
    "why",
    "did",
    "the",
    "by",
)


def detect_language(text: str) -> str:
    """``uz``, ``en`` or ``ru``. The answer language is still the UI locale."""
    raw = text or ""
    if CYRILLIC_RE.search(raw):
        return "ru"
    key = match_key(normalize_text(raw))
    tokens = set(key.split())
    uz = sum(1 for w in UZ_HINTS if w in tokens or w in key)
    en = sum(1 for w in EN_HINTS if w in tokens)
    if en > uz:
        return "en"
    return "uz"
