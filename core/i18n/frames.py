"""Message frames: insight text without free-form generation (spec 5.4).

One frame exists for every (finding type x direction x reading level) in both languages
(``frames_uz.json`` and ``frames_en.json``). A frame has a headline, a one-sentence meaning,
one concrete next step and, for the expert level, details. Dynamic values (segment names,
column names, dates) are inserted as ``«{segment}»`` or after a colon and never get a suffix.

Levels: ``simple`` (default), ``expert`` (adds details) and ``simpler`` (the "Explain simpler"
button: at most 12 words per sentence and one number).
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from functools import cache
from pathlib import Path

LOCALES = ("uz", "en")
LEVELS = ("simple", "expert", "simpler")
DATA_DIR = Path(__file__).parent
PLACEHOLDER_RE = re.compile(r"\{(\w+)\}")
TEXT_FIELDS = ("headline", "meaning", "next_step", "details")


@dataclass(frozen=True)
class FindingSpec:
    """Directions a finding type can have and the values its frames may use."""

    directions: tuple[str, ...]
    params: tuple[str, ...]
    expert_params: tuple[str, ...] = ()


UP_DOWN = ("up", "down")
NEUTRAL = ("neutral",)

FINDING_TYPES: dict[str, FindingSpec] = {
    "trend": FindingSpec(UP_DOWN, ("metric", "pct", "compare"), ("previous", "current")),
    "anomaly": FindingSpec(UP_DOWN, ("metric", "date", "value", "typical"), ("z", "n")),
    "concentration": FindingSpec(
        NEUTRAL, ("metric_short", "metric_gen", "share", "segment"), ("top3",)
    ),
    "driver": FindingSpec(UP_DOWN, ("metric", "share", "segment"), ("change",)),
    "segment_gap": FindingSpec(
        NEUTRAL, ("best", "worst", "metric", "ratio"), ("best_value", "worst_value")
    ),
    "segment_momentum": FindingSpec(
        UP_DOWN, ("segment", "metric", "pct", "compare"), ("previous", "current")
    ),
    "churn": FindingSpec(UP_DOWN, ("rate", "baseline"), ("customers",)),
    "data_quality": FindingSpec(NEUTRAL, ("column", "pct"), ("rows",)),
    "funnel_drop": FindingSpec(
        NEUTRAL, ("stage_from", "stage_to", "rate", "typical"), ("entered",)
    ),
    "stalled": FindingSpec(NEUTRAL, ("share", "days"), ("deals", "value")),
    "win_rate_gap": FindingSpec(NEUTRAL, ("best", "worst", "ratio"), ("n_best", "n_worst")),
    "cycle_change": FindingSpec(UP_DOWN, ("days", "pct", "compare"), ("previous_days",)),
    "outliers": FindingSpec(NEUTRAL, ("column", "count", "share"), ("extreme", "iqr_factor")),
    "correlation": FindingSpec(UP_DOWN, ("a", "b"), ("rho", "n")),
    "group_difference": FindingSpec(NEUTRAL, ("metric", "high", "low", "pct"), ("test", "effect")),
    "skewness": FindingSpec(NEUTRAL, ("column",), ("skew", "n")),
    "missing_pattern": FindingSpec(NEUTRAL, ("column", "category", "pct"), ("v", "p")),
    "trend_regression": FindingSpec(UP_DOWN, ("column", "change"), ("r2", "p")),
}

# Parameters that carry a number. A "simpler" frame may use at most one of them.
NUMERIC_PARAMS = frozenset(
    {
        "pct", "share", "value", "typical", "ratio", "rate", "baseline", "days", "count",
        "change", "extreme", "previous", "current", "z", "n", "top3", "customers", "rows",
        "entered", "deals", "n_best", "n_worst", "previous_days", "iqr_factor", "rho", "effect",
        "skew", "v", "p", "r2", "best_value", "worst_value",
    }
)

# The closed set for ``{compare}``.
COMPARE_PHRASES = {
    "uz": {
        "prev_week": "o\u02bbtgan haftaga nisbatan",
        "prev_month": "o\u02bbtgan oyga nisbatan",
        "prev_quarter": "o\u02bbtgan chorakka nisbatan",
        "prev_year": "o\u02bbtgan yilga nisbatan",
        "prev_period": "oldingi davrga nisbatan",
    },
    "en": {
        "prev_week": "compared with last week",
        "prev_month": "compared with last month",
        "prev_quarter": "compared with last quarter",
        "prev_year": "compared with last year",
        "prev_period": "compared with the previous period",
    },
}


class FrameError(ValueError):
    """A frame is missing or a value it needs was not given."""


@dataclass(frozen=True)
class Card:
    """The texts of one insight card."""

    headline: str
    meaning: str
    next_step: str
    details: str = ""


def frame_key(finding_type: str, direction: str, level: str) -> str:
    """'trend.down.simple'."""
    return f"{finding_type}.{direction}.{level}"


def all_frame_keys() -> list[str]:
    """Every key that must exist in each language."""
    return [
        frame_key(finding_type, direction, level)
        for finding_type, spec in FINDING_TYPES.items()
        for direction in spec.directions
        for level in LEVELS
    ]


@cache
def load_frames(locale: str) -> dict[str, dict[str, object]]:
    """The frames of one language, keyed like ``trend.down.simple``."""
    if locale not in LOCALES:
        raise FrameError(f"unsupported locale: {locale!r}")
    data = json.loads((DATA_DIR / f"frames_{locale}.json").read_text(encoding="utf-8"))
    return data["frames"]


def compare_text(key: str, locale: str) -> str:
    """The ready phrase for a comparison period, e.g. 'o\u02bbtgan oyga nisbatan'."""
    try:
        return COMPARE_PHRASES[locale][key]
    except KeyError as exc:
        raise FrameError(f"unknown comparison phrase: {key!r}") from exc


def placeholders(text: str) -> set[str]:
    """Names inside ``{...}`` in a frame text."""
    return set(PLACEHOLDER_RE.findall(text))


def render_card(
    finding_type: str,
    direction: str,
    level: str,
    locale: str,
    values: dict[str, object],
) -> Card:
    """Fill a frame. Values are inserted as given (format numbers first with ``formats``)."""
    key = frame_key(finding_type, direction, level)
    frame = load_frames(locale).get(key)
    if frame is None:
        raise FrameError(f"no frame {key!r} in {locale}")
    rendered = {}
    for field in TEXT_FIELDS:
        text = str(frame.get(field, ""))
        missing = placeholders(text) - set(values)
        if missing:
            raise FrameError(f"frame {key!r} needs: {', '.join(sorted(missing))}")
        rendered[field] = PLACEHOLDER_RE.sub(lambda m: str(values[m.group(1)]), text)
    return Card(**rendered)
