"""Glossary: technical term -> plain explanation (spec 5.5).

``glossary_uz.json`` and ``glossary_en.json`` have the same keys. An entry looks like
``{"term": ..., "plain": ..., "aliases": [...], "reviewed": false}``. ``term`` is how the term
is written in that language; ``aliases`` are other spellings that should find the entry
(they are compared in ``match_key`` form, so apostrophes and Cyrillic do not matter).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from functools import cache
from pathlib import Path

from core.nlu.normalize import match_key

LOCALES = ("uz", "en")
DATA_DIR = Path(__file__).parent


@dataclass(frozen=True)
class GlossaryEntry:
    """One glossary item in one language."""

    key: str
    term: str
    plain: str
    aliases: tuple[str, ...]
    reviewed: bool


@cache
def load_glossary(locale: str) -> dict[str, GlossaryEntry]:
    """All entries of a language by key."""
    if locale not in LOCALES:
        raise ValueError(f"unsupported locale: {locale!r}")
    raw = json.loads((DATA_DIR / f"glossary_{locale}.json").read_text(encoding="utf-8"))
    return {
        key: GlossaryEntry(
            key=key,
            term=item["term"],
            plain=item["plain"],
            aliases=tuple(item.get("aliases", [])),
            reviewed=bool(item.get("reviewed", False)),
        )
        for key, item in raw["entries"].items()
    }


@cache
def _index(locale: str) -> dict[str, str]:
    index: dict[str, str] = {}
    for key, entry in load_glossary(locale).items():
        for spelling in (entry.term, key.replace("_", " "), *entry.aliases):
            index.setdefault(match_key(spelling), key)
    return index


def find_entry(text: str, locale: str) -> GlossaryEntry | None:
    """The entry for a term or one of its aliases, or ``None``."""
    key = _index(locale).get(match_key(text))
    return load_glossary(locale)[key] if key else None


def explain(text: str, locale: str) -> str | None:
    """The plain explanation of a term, or ``None`` when it is not in the glossary."""
    entry = find_entry(text, locale)
    return entry.plain if entry else None
