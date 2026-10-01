"""Direction words (spec 5.4 rule 4): a closed set per language so polarity can be verified.

Uzbek up: oshdi, ko'paydi, o'sdi, yaxshilandi.  Down: kamaydi, pasaydi, tushdi, yomonlashdi.
The patterns also match the other tenses used in frames (oshadi, oshib, kamayib, ...).
Uzbek letters follow the display orthography (oʻ with U+02BB); text is passed through
``match`` after apostrophe normalization, so ``o'sdi`` and ``oʻsdi`` both count.
"""

from __future__ import annotations

import re

from core.nlu.normalize import normalize_text

_UZ_UP = (
    r"\bosh(?:di|adi|moqda|ib)\b",
    r"\bko'pay\w*",
    r"\bo's(?:di|adi|moqda|ib)\b",
    r"\byaxshilan\w*",
)
_UZ_DOWN = (
    r"\bkamay\w*",
    r"\bpasay\w*",
    r"\btush(?:di|adi|moqda|ib)\b",
    r"\byomonlash\w*",
)
_EN_UP = (
    r"\b(?:rose|grew|grow|grows|growing)\b",
    r"\bincreas(?:e|es|ed|ing)\b",
    r"\bimprov(?:e|es|ed|ing)\b",
)
_EN_DOWN = (
    r"\b(?:fell|dropped|falls|falling)\b",
    r"\bdecreas(?:e|es|ed|ing)\b",
    r"\bworsen(?:s|ed|ing)?\b",
)

_PATTERNS = {
    ("uz", "up"): re.compile("|".join(_UZ_UP)),
    ("uz", "down"): re.compile("|".join(_UZ_DOWN)),
    ("en", "up"): re.compile("|".join(_EN_UP)),
    ("en", "down"): re.compile("|".join(_EN_DOWN)),
}


def direction_words(text: str, locale: str) -> dict[str, list[str]]:
    """The up and down words found in a text: ``{'up': [...], 'down': [...]}``."""
    normal = normalize_text(text)
    return {
        direction: [m.group(0) for m in _PATTERNS[(locale, direction)].finditer(normal)]
        for direction in ("up", "down")
    }


def has_direction(text: str, locale: str, direction: str) -> bool:
    """True when the text contains at least one word of the given direction."""
    return bool(direction_words(text, locale)[direction])


def polarity_of(text: str, locale: str) -> str | None:
    """'up' or 'down' when only one kind of word occurs, 'mixed' when both, None when neither."""
    found = direction_words(text, locale)
    if found["up"] and found["down"]:
        return "mixed"
    if found["up"]:
        return "up"
    if found["down"]:
        return "down"
    return None
