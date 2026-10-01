"""Text normalization for matching Uzbek, Russian and English input (spec 5.2).

Users type ``o'tgan oy``, ``oʻtgan oy`` or ``o‘tgan oy``; all must match. Two functions:

* ``normalize_text``: the canonical form used for matching and routing (lower case, one
  apostrophe character, no punctuation, single spaces).
* ``match_key``: a looser key for dictionary lookup that also drops apostrophes and turns
  Cyrillic into Latin, so ``mas'ul`` = ``masul`` and ``сумма`` = ``summa``.

``to_display_orthography`` goes the other way for text shown to people: ``o'`` and ``g'``
become ``oʻ`` and ``gʻ`` (U+02BB), any other apostrophe stays an ASCII ``'`` (tutuq belgisi).
"""

from __future__ import annotations

import re
import unicodedata

CANONICAL_APOSTROPHE = "'"
MODIFIER_TURNED_COMMA = "\u02bb"  # the letter in oʻ and gʻ
# ASCII, left/right single quotes, U+02BB, U+02BC, grave, acute, prime, U+02B9, fullwidth.
APOSTROPHES = "'\u2018\u2019\u02bb\u02bc`\u00b4\u2032\u02b9\uff07"
_APOSTROPHE_RE = re.compile(f"[{re.escape(APOSTROPHES)}]")
_LETTER = r"[^\W\d_]"
_STRAY_APOSTROPHE_RE = re.compile(f"(?<!{_LETTER})'|'(?!{_LETTER})")
_INNER_APOSTROPHE_RE = re.compile(f"({_LETTER})[{re.escape(APOSTROPHES)}](?={_LETTER})")
_DECIMAL_MARK_RE = re.compile(r"(?<!\d)[.,]|[.,](?!\d)")
_PUNCTUATION_RE = re.compile(r"[^\w'%.,]|_", re.UNICODE)
_SPACES_RE = re.compile(r"\s+")

_CYRILLIC = {
    "\u0430": "a",
    "\u0431": "b",
    "\u0432": "v",
    "\u0433": "g",
    "\u0434": "d",
    "\u0435": "e",
    "\u0451": "yo",
    "\u0436": "j",
    "\u0437": "z",
    "\u0438": "i",
    "\u0439": "y",
    "\u043a": "k",
    "\u043b": "l",
    "\u043c": "m",
    "\u043d": "n",
    "\u043e": "o",
    "\u043f": "p",
    "\u0440": "r",
    "\u0441": "s",
    "\u0442": "t",
    "\u0443": "u",
    "\u0444": "f",
    "\u0445": "x",
    "\u0446": "ts",
    "\u0447": "ch",
    "\u0448": "sh",
    "\u0449": "sh",
    "\u044a": "",
    "\u044b": "i",
    "\u044c": "",
    "\u044d": "e",
    "\u044e": "yu",
    "\u044f": "ya",
    # Uzbek Cyrillic letters
    "\u045e": "o",
    "\u049b": "q",
    "\u0493": "g",
    "\u04b3": "h",
}


def normalize_apostrophes(text: str, canonical: str = CANONICAL_APOSTROPHE) -> str:
    """Replace every apostrophe variant by one character."""
    return _APOSTROPHE_RE.sub(canonical, text)


def lowercase(text: str) -> str:
    """Lower case that is safe for Uzbek Latin: no dotted capital I leftovers."""
    return text.replace("\u0130", "i").lower()


def normalize_text(text: str) -> str:
    """Canonical form for matching.

    Steps: Unicode NFC, lower case, one apostrophe character kept only inside words,
    punctuation removed (a '.' or ',' inside a number stays), spaces collapsed.
    """
    text = unicodedata.normalize("NFC", text or "")
    text = lowercase(normalize_apostrophes(text))
    text = _DECIMAL_MARK_RE.sub(" ", text)
    text = _PUNCTUATION_RE.sub(" ", text.replace("_", " "))
    text = _STRAY_APOSTROPHE_RE.sub(" ", text)
    return _SPACES_RE.sub(" ", text).strip()


def cyrillic_to_latin(text: str) -> str:
    """Transliterate Russian and Uzbek Cyrillic to Uzbek Latin letters (lower case input)."""
    return "".join(_CYRILLIC.get(char, char) for char in text)


def match_key(text: str) -> str:
    """Key for synonym and column-name lookup: normalized, Latin, no apostrophes."""
    key = cyrillic_to_latin(normalize_text(text))
    return _SPACES_RE.sub(" ", key.replace(CANONICAL_APOSTROPHE, "")).strip()


def to_display_orthography(text: str) -> str:
    """Show Uzbek with the correct letters: oʻ and gʻ (U+02BB), tutuq belgisi as ``'``."""

    def fix(match: re.Match[str]) -> str:
        letter = match.group(1)
        return letter + (MODIFIER_TURNED_COMMA if letter in "oOgG" else CANONICAL_APOSTROPHE)

    return _INNER_APOSTROPHE_RE.sub(fix, text)
