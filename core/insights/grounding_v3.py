"""Grounding for Uzbek and English text: numbers must come from the card, direction must not flip.

``grounding.py`` (v2) understands English number styles only. The cards of v3 are written in
Uzbek or English, so this module also reads ``1 234,56``, ``12,8 mln``, ``8,2%``, ``12.8M`` and
``25 foiz``. Group separators are spaces (also U+00A0 and U+202F); the decimal separator is
decided by the language of the text: a comma in Uzbek, a dot in English.

Rules (also used to accept or refuse the optional LLM polish):

- A number in a text is grounded when it equals a number of the original text at the precision
  shown (``12,8`` for ``12,84``), or lies within 0.5% of it. The sign is ignored.
- Ignored: whole numbers 0 to 10, years 1900 to 2100, dates (``2026-09-12``, ``2026-09``) and
  text inside « », quotes and glossary tokens, because names may contain digits (``Store 03``).
- Direction words (``oshdi``, ``kamaydi``, ``rose``, ``fell`` ...) of a rewritten text must be
  the same kind as in the original: ``up`` stays ``up``. If the original has a direction and the
  rewrite has none the rewrite is refused, because its meaning can no longer be checked.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from core.i18n.jargon import TOKEN_RE
from core.i18n.polarity import direction_words

RELATIVE_TOLERANCE = 0.005
GROUP_SPACES = " \u00a0\u202f\u2009"
SCALES = {
    "ming": 1e3,
    "mln": 1e6,
    "mlrd": 1e9,
    "trln": 1e12,
    "k": 1e3,
    "thousand": 1e3,
    "million": 1e6,
    "billion": 1e9,
    "trillion": 1e12,
    "m": 1e6,
    "b": 1e9,
    "t": 1e12,
}
PERCENT_WORDS = {"%", "foiz"}
DATE_RE = re.compile(r"\b\d{4}-\d{2}(?:-\d{2})?\b")
QUOTED_RE = re.compile(r"\u00ab[^\u00bb]*\u00bb|\"[^\"]*\"|\u201c[^\u201d]*\u201d")
NUMBER_RE = re.compile(
    r"(?<![\w.,])(?P<num>"
    r"\d{1,3}(?:[ \u00a0\u202f\u2009]\d{3})+(?:[.,]\d+)?"
    r"|\d{1,3}(?:\.\d{3})+(?:,\d+)?"
    r"|\d{1,3}(?:,\d{3})+(?:\.\d+)?"
    r"|\d+(?:[.,]\d+)?)"
    r"(?:[ \u00a0\u202f]?(?P<suffix>%|foiz\b|mlrd\b|mln\b|trln\b|ming\b|thousand\b|million\b"
    r"|billion\b|trillion\b|[KMBTk]\b))?"
)


@dataclass(frozen=True)
class Number:
    """A number read from text."""

    text: str
    value: float
    mantissa: float
    scale: float
    decimals: int
    marked: bool  # has a percent sign or scale word


def to_float(raw: str, locale: str) -> tuple[float, int]:
    """The value of a written number and how many decimals it shows."""
    text = re.sub(f"[{GROUP_SPACES}]", "", raw)
    has_comma, has_dot = "," in text, "." in text
    decimal = ""
    if has_comma and has_dot:
        decimal = "," if text.rfind(",") > text.rfind(".") else "."
    elif has_comma:
        grouped = re.fullmatch(r"\d{1,3}(,\d{3})+", text) is not None
        decimal = "," if locale == "uz" or not grouped else ""
    elif has_dot:
        grouped = re.fullmatch(r"\d{1,3}(\.\d{3})+", text) is not None
        decimal = "." if locale == "en" or not grouped else ""
    other = "." if decimal == "," else ","
    text = text.replace(other, "")
    decimals = len(text.split(decimal)[1]) if decimal else 0
    return float(text.replace(decimal, ".") if decimal else text), decimals


def clean(text: str, names: list[str] | None = None) -> str:
    """Remove what must not be read as numbers: names, quoted text, tokens and dates."""
    for name in sorted(names or [], key=len, reverse=True):
        if name:
            text = re.sub(re.escape(name), " ", text, flags=re.IGNORECASE)
    text = TOKEN_RE.sub(" ", text)
    return DATE_RE.sub(" ", QUOTED_RE.sub(" ", text))


def numbers_in(text: str, locale: str, names: list[str] | None = None) -> list[Number]:
    """All numbers of a text with their meaning."""
    found = []
    for match in NUMBER_RE.finditer(clean(text, names)):
        mantissa, decimals = to_float(match.group("num"), locale)
        suffix = match.group("suffix") or ""
        scale = SCALES.get(suffix.lower(), 1.0) if suffix not in PERCENT_WORDS else 1.0
        found.append(
            Number(
                match.group(0).strip(), mantissa * scale, mantissa, scale, decimals, bool(suffix)
            )
        )
    return found


def is_ignored(number: Number) -> bool:
    """Whole numbers 0-10 and years 1900-2100 are not checked."""
    if number.marked or number.decimals or number.scale != 1.0:
        return False
    whole = number.mantissa
    return 0 <= whole <= 10 or 1900 <= whole <= 2100


def same_number(number: Number, other: Number) -> bool:
    """True when ``number`` is ``other`` at the precision ``number`` shows, or within 0.5%."""
    reference = abs(other.value)
    shown = round(reference / number.scale, number.decimals)
    if abs(shown - number.mantissa) < 1e-9:
        return True
    if reference == 0:
        return number.value == 0
    return abs(abs(number.value) - reference) / reference <= RELATIVE_TOLERANCE


def ungrounded(text: str, original: str, locale: str, names: list[str] | None = None) -> list[str]:
    """Numbers in ``text`` that do not occur in ``original``."""
    allowed = numbers_in(original, locale, names)
    return [
        n.text
        for n in numbers_in(text, locale, names)
        if not is_ignored(n) and not any(same_number(n, a) for a in allowed)
    ]


def polarity_problem(original: str, rewritten: str, locale: str) -> str | None:
    """A message when the rewrite turns the direction around or hides it."""
    before, after = direction_words(original, locale), direction_words(rewritten, locale)
    for kind in ("up", "down"):
        if after[kind] and not before[kind]:
            return f"the direction word '{after[kind][0]}' was added"
    if (before["up"] or before["down"]) and not (after["up"] or after["down"]):
        return "the direction word is missing, so the meaning cannot be checked"
    return None


def problems(
    original: str, rewritten: str, locale: str, names: list[str] | None = None
) -> list[str]:
    """Everything wrong with a rewrite of ``original``: new numbers and a changed direction."""
    found = [
        f"number not in the original: {n}" for n in ungrounded(rewritten, original, locale, names)
    ]
    flip = polarity_problem(original, rewritten, locale)
    return [*found, flip] if flip else found
