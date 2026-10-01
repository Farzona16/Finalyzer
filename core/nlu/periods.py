"""Named months, 'last month', years: a closed set of periods the compiler can turn into dates."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date

from core.i18n.formats import MONTHS
from core.nlu.normalize import match_key, normalize_text


def _add_months(value: date, months: int) -> date:
    index = value.year * 12 + value.month - 1 + months
    return date(index // 12, index % 12 + 1, 1)


MONTH_INDEX: dict[str, int] = {}
for names in MONTHS.values():
    for i, name in enumerate(names, start=1):
        MONTH_INDEX[match_key(name)] = i
MONTH_INDEX.update(
    {
        "jan": 1,
        "feb": 2,
        "mar": 3,
        "apr": 4,
        "jun": 6,
        "jul": 7,
        "aug": 8,
        "sep": 9,
        "sept": 9,
        "oct": 10,
        "nov": 11,
        "dec": 12,
        "sentyabr": 9,
        "oktyabr": 10,
    }
)
YEAR_RE = re.compile(r"\b(20\d{2})\b")
ISO_MONTH_RE = re.compile(r"\b(20\d{2})-(\d{2})\b")
LAST_MONTH_RE = re.compile(r"\b(oxirgi oy(?:da)?|otgan oy(?:da)?|last month|previous month)\b")
THIS_YEAR_RE = re.compile(r"\b(bu yil|this year|joriy yil)\b")
LAST_YEAR_RE = re.compile(r"\b(otgan yil(?!ga)|last year|previous year)\b")


@dataclass(frozen=True)
class Period:
    """A closed date range ``[start, end)`` the compiler can put in a WHERE clause."""

    kind: str
    start: date | None = None
    end: date | None = None
    label: str = ""


def bounds_of_month(year: int, month: int) -> tuple[date, date]:
    """``[first of month, first of next month)``."""
    start = date(year, month, 1)
    return start, _add_months(start, 1)


def last_month_of(latest: date) -> tuple[date, date]:
    """The calendar month of ``latest`` (the last month present in the data)."""
    return bounds_of_month(latest.year, latest.month)


def parse_period(text: str, latest: date | None) -> Period | None:
    """A period mentioned in the question, resolved against the latest date in the data."""
    key = match_key(normalize_text(text))
    raw = normalize_text(text)
    iso = ISO_MONTH_RE.search(raw)
    if iso:
        year, month = int(iso.group(1)), int(iso.group(2))
        if 1 <= month <= 12:
            start, end = bounds_of_month(year, month)
            return Period("iso_month", start, end, f"{year:04d}-{month:02d}")
    if LAST_MONTH_RE.search(key) and latest is not None:
        start, end = last_month_of(latest)
        return Period("last_month", start, end, f"{start.year:04d}-{start.month:02d}")
    if THIS_YEAR_RE.search(key) and latest is not None:
        start = date(latest.year, 1, 1)
        return Period("this_year", start, date(latest.year + 1, 1, 1), str(latest.year))
    year_match = YEAR_RE.search(key)
    year = int(year_match.group(1)) if year_match else (latest.year if latest else None)
    for name, month in MONTH_INDEX.items():
        if re.search(rf"\b{re.escape(name)}(?:da)?\b", key) and year is not None:
            start, end = bounds_of_month(year, month)
            return Period("named_month", start, end, f"{year:04d}-{month:02d}")
    if year_match and not any(n in key for n in MONTH_INDEX):
        year = int(year_match.group(1))
        return Period("year", date(year, 1, 1), date(year + 1, 1, 1), str(year))
    if LAST_YEAR_RE.search(key) and latest is not None:
        year = latest.year - 1
        return Period("last_year", date(year, 1, 1), date(year + 1, 1, 1), str(year))
    return None
