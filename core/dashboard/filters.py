"""Slicers: turn a ``FilterSpec`` into safe SQL and apply it to the ``data`` view.

Every literal is escaped here (single quotes doubled, numbers through ``float()``, dates
through ``date.fromisoformat``) and every column name is checked against the flat view, so a
category value such as ``x' OR '1'='1`` is just an unusual value that matches no rows.
"""

from __future__ import annotations

import hashlib
import math
from datetime import date

import duckdb

from core.errors import ValidationError
from core.ingest.profiler import quote_identifier
from core.models import ColumnInfo, DatasetModel, FilterSpec

MAX_SLICERS = 4
MAX_SLICER_DISTINCT = 30
ID_ROLES = {"id", "order_id", "customer_id"}


def sql_string(value: str) -> str:
    """A single-quoted SQL string literal."""
    text = str(value)
    if "\x00" in text:
        raise ValidationError("Filter values may not contain control characters.")
    return "'" + text.replace("'", "''") + "'"


def sql_number(value: float) -> str:
    """A numeric literal from a value that is converted with ``float()``."""
    number = float(value)
    if not math.isfinite(number):
        raise ValidationError("Filter numbers must be finite.")
    return repr(number)


def sql_date(value: date) -> str:
    """A DuckDB date literal."""
    return f"DATE '{date.fromisoformat(value.isoformat()).isoformat()}'"


def find_column(model: DatasetModel, name: str) -> ColumnInfo:
    """The flat-view column called ``name`` or a ``ValidationError``."""
    for column in model.columns:
        if column.name == name:
            return column
    raise ValidationError(f"Unknown column '{name}' in filter.")


def date_condition(model: DatasetModel, start: date, end: date) -> str:
    """Condition that keeps rows between two dates (inclusive)."""
    if "date" not in model.roles:
        raise ValidationError("A date filter needs a date column, but none is mapped.")
    column = quote_identifier(model.roles["date"])
    return f"{column} >= {sql_date(start)} AND {column} <= {sql_date(end)}"


def conditions(
    spec: FilterSpec, model: DatasetModel, date_override: tuple[date, date] | None = None
) -> list[str]:
    """All filter conditions of a spec as SQL text (empty selections are ignored)."""
    parts: list[str] = []
    date_range = date_override or spec.date_range
    if date_range:
        parts.append(date_condition(model, date_range[0], date_range[1]))
    for name, values in spec.categorical.items():
        find_column(model, name)
        if values:
            items = ", ".join(sql_string(v) for v in values)
            parts.append(f"{quote_identifier(name)} IN ({items})")
    for name, (low, high) in spec.numeric.items():
        if find_column(model, name).dtype not in ("integer", "float"):
            raise ValidationError(f"Column '{name}' is not numeric.")
        parts.append(
            f"{quote_identifier(name)} BETWEEN {sql_number(min(low, high))} "
            f"AND {sql_number(max(low, high))}"
        )
    return parts


def where_sql(
    spec: FilterSpec, model: DatasetModel, date_override: tuple[date, date] | None = None
) -> str:
    """``WHERE ...`` text for the spec, or an empty string when nothing is filtered.

    ``date_override`` replaces the spec's date range (used for previous-period queries on
    ``base`` where the other slicers still apply).
    """
    parts = conditions(spec, model, date_override)
    return "WHERE " + " AND ".join(parts) if parts else ""


def apply_filters(con: duckdb.DuckDBPyConnection, spec: FilterSpec, model: DatasetModel) -> int:
    """Recreate the ``data`` view from ``base`` with the slicers. Returns the row count."""
    where = where_sql(spec, model)
    con.execute(f"CREATE OR REPLACE VIEW data AS SELECT * FROM base {where}")
    return int(con.execute("SELECT COUNT(*) FROM data").fetchone()[0])


def is_active(spec: FilterSpec) -> bool:
    """True when at least one slicer restricts the data."""
    return bool(spec.date_range or any(spec.categorical.values()) or spec.numeric)


def filter_hash(spec: FilterSpec) -> str:
    """Stable short hash of a filter spec, for cache keys."""
    text = spec.model_dump_json()
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def describe_filters(spec: FilterSpec, include_values: bool = True) -> str:
    """One line listing the active filters. Category values can be left out for privacy."""
    parts: list[str] = []
    if spec.date_range:
        parts.append(f"dates {spec.date_range[0]} to {spec.date_range[1]}")
    for name, values in spec.categorical.items():
        if values:
            shown = ", ".join(values) if include_values else f"{len(values)} selected"
            parts.append(f"{name}: {shown}")
    for name, (low, high) in spec.numeric.items():
        parts.append(f"{name}: {min(low, high):g} to {max(low, high):g}")
    return "; ".join(parts) if parts else "none"


def slicer_score(column: ColumnInfo) -> tuple[bool, int]:
    """Sort key: columns with 3-30 distinct values first, then closest to 8 values."""
    return (not 3 <= column.distinct <= MAX_SLICER_DISTINCT, abs(column.distinct - 8))


def choose_slicers(model: DatasetModel, limit: int = MAX_SLICERS) -> list[str]:
    """Up to four low-cardinality text columns that make useful multi-select slicers."""
    id_columns = {model.roles[r] for r in ID_ROLES if r in model.roles}
    candidates = [
        c
        for c in model.columns
        if c.dtype == "text"
        and not c.is_pii
        and c.name not in id_columns
        and c.categories
        and 2 <= c.distinct <= MAX_SLICER_DISTINCT
        and c.null_pct < 50
    ]
    return [c.name for c in sorted(candidates, key=slicer_score)[:limit]]
