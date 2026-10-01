"""Small wording helpers shared by detectors and narrators."""

from __future__ import annotations

from core.dashboard.kpi import format_value


def direction_of(change: float) -> str:
    """``up`` or ``down`` from the sign of a change."""
    return "up" if change > 0 else "down"


def assessment(direction: str, good_direction: str) -> str:
    """``good``, ``bad`` or ``neutral`` for a move in ``direction`` of a metric."""
    if good_direction == "neutral":
        return "neutral"
    return "good" if direction == good_direction else "bad"


def verb(direction: str) -> str:
    """Past-tense verb for a move."""
    return "rose" if direction == "up" else "fell"


def pct(value: float, digits: int = 1) -> str:
    """Unsigned percent text such as ``25.9%``."""
    return f"{abs(value):.{digits}f}%"


def value_text(value: float, unit: str) -> str:
    """A metric value formatted for its unit."""
    return format_value(value, unit)


def humanize(name: str) -> str:
    """``store_name`` -> ``store name``."""
    return name.replace("_", " ")
