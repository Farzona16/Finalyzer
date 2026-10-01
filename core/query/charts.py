"""Deterministic chart choice from the shape of a query result.

The LLM's ``chart_hint`` is advisory only: it can ask for a table, nothing else.
"""

from __future__ import annotations

import re
from typing import Any, Literal

from pydantic import BaseModel

from core.models import QueryResult

ChartKind = Literal["kpi", "line", "bar", "hbar", "grouped_bar", "table"]
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}([ T]\d{2}:\d{2}(:\d{2}(\.\d+)?)?)?$")
MAX_BARS = 15
HORIZONTAL_FROM = 9  # more than 8 categories -> horizontal bars


class ChartSpec(BaseModel):
    """What to draw: the kind, the x column, the y columns and an optional row limit."""

    kind: ChartKind
    x: str | None = None
    y: list[str] = []
    limit: int | None = None


def column_kind(values: list[Any]) -> str:
    """Return ``date``, ``number``, ``text`` or ``other`` for a result column."""
    present = [v for v in values if v is not None]
    if not present:
        return "other"
    if all(isinstance(v, int | float) and not isinstance(v, bool) for v in present):
        return "number"
    if all(isinstance(v, str) for v in present):
        return "date" if all(DATE_RE.match(v) for v in present) else "text"
    return "other"


def column_kinds(result: QueryResult) -> list[str]:
    """Kind of every column of a result."""
    return [column_kind([row[i] for row in result.rows]) for i in range(len(result.columns))]


def choose_chart(result: QueryResult, hint: str | None = None) -> ChartSpec:
    """Pick the chart for a result: KPI, line, bar, grouped bar or table."""
    columns = result.columns
    if result.row_count == 0 or hint == "table":
        return ChartSpec(kind="table")
    if result.row_count == 1 and len(columns) == 1:
        return ChartSpec(kind="kpi", y=[columns[0]])
    kinds = column_kinds(result)
    numbers = [c for c, k in zip(columns, kinds, strict=True) if k == "number"]
    if len(columns) >= 2 and kinds[0] == "date" and kinds[1:] == ["number"] * (len(columns) - 1):
        if len(columns) <= 4:
            return ChartSpec(kind="line", x=columns[0], y=columns[1:])
    if len(columns) >= 2 and kinds[0] == "text" and kinds[1:] == ["number"] * (len(columns) - 1):
        if len(numbers) == 1:
            kind = "hbar" if result.row_count >= HORIZONTAL_FROM else "bar"
            return ChartSpec(kind=kind, x=columns[0], y=numbers, limit=MAX_BARS)
        if len(numbers) <= 3:
            return ChartSpec(kind="grouped_bar", x=columns[0], y=numbers, limit=MAX_BARS)
    return ChartSpec(kind="table")
