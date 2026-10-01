"""Role and column synonyms in Uzbek, Russian and English (spec 5.7, Appendix C).

Matching uses ``match_key``: apostrophes are ignored and Cyrillic is turned into Latin, so
``Mas'ul``, ``masul`` and ``Масул`` find the same role, and ``сумма`` finds ``summa``.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

from core.nlu.normalize import match_key

DATA_FILE = Path(__file__).with_name("synonyms_uz_ru_en.json")
LANGUAGES = ("uz", "ru", "en")


@lru_cache(maxsize=1)
def load() -> dict:
    """The raw synonym file."""
    return json.loads(DATA_FILE.read_text(encoding="utf-8"))


@lru_cache(maxsize=1)
def _role_index() -> dict[str, str]:
    index: dict[str, str] = {}
    for role, by_language in load()["roles"].items():
        index.setdefault(match_key(role), role)
        for language in LANGUAGES:
            for word in by_language.get(language, []):
                index.setdefault(match_key(word), role)
    return index


def role_for_column(name: str) -> str | None:
    """The role a column name stands for, or ``None``.

    An exact match wins. Otherwise a synonym of at least 4 letters whose words all occur in the
    column name counts (``sotuv_sanasi_2026`` -> date); the longest such synonym wins.
    """
    key = match_key(name)
    index = _role_index()
    if key in index:
        return index[key]
    tokens = set(key.split())
    best: tuple[int, str] | None = None
    for synonym, role in index.items():
        words = synonym.split()
        if len(synonym) >= 4 and set(words) <= tokens and (best is None or len(synonym) > best[0]):
            best = (len(synonym), role)
    return best[1] if best else None


@lru_cache(maxsize=1)
def _stage_index() -> dict[str, int]:
    index: dict[str, int] = {}
    for position, item in enumerate(load()["stage_order"]):
        rank = item.get("rank", position)
        for language in LANGUAGES:
            for word in item.get(language, []):
                index.setdefault(match_key(word), rank)
    return index


def stage_rank(name: str) -> int | None:
    """Position of a pipeline stage (lead=0 ... won and lost share the last place), or ``None``."""
    return _stage_index().get(match_key(name))
