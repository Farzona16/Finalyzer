"""Text lookup: every string the user sees comes from ``app/i18n/en.py``."""

from __future__ import annotations

from app.i18n.en import TEXT


def t(key: str, **values: object) -> str:
    """Return the text for ``key`` with ``{placeholders}`` filled in."""
    return TEXT[key].format(**values) if values else TEXT[key]
