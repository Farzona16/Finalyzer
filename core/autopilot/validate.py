"""Validation of every autopilot metric (spec 6.1, step 4).

A metric is kept only when it compiles, runs inside the time limit, returns one finite number,
is not constant, is not mostly empty, has a non-zero denominator, selects at least one row and
matches an independent pandas computation within 1e-6 (relative). Failures are dropped silently;
the reason is returned for the log, never shown to the user.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass

import duckdb
import pandas as pd

from core.autopilot.dsl import (
    NUMERIC_AGGS,
    Clause,
    Context,
    DslError,
    Spec,
    compile_sql,
)
from core.config import Settings
from core.errors import UserFacingError
from core.ingest.profiler import quote_identifier
from core.query.executor import execute_query

logger = logging.getLogger("finalyzer.autopilot")

MAX_NULL_PCT = 50.0
PARITY_TOLERANCE = 1e-6


@dataclass(frozen=True)
class Validation:
    """Outcome for one metric. ``reason`` is for the log only."""

    ok: bool
    reason: str = ""
    value: float | None = None


def spec_columns(spec: Spec) -> set[str]:
    """Every column a spec reads (including its filters and ratio parts)."""
    if spec.agg == "ratio":
        return spec_columns(spec.numerator) | spec_columns(spec.denominator)
    return set(spec.columns) | {c.column for c in spec.filters}


def load_frame(con: duckdb.DuckDBPyConnection, columns: set[str]) -> pd.DataFrame:
    """The needed columns of the ``data`` view as a pandas frame (for the parity check)."""
    if not columns:
        return pd.DataFrame(index=range(con.execute("SELECT COUNT(*) FROM data").fetchone()[0]))
    names = ", ".join(quote_identifier(c) for c in sorted(columns))
    return con.execute(f"SELECT {names} FROM data").df()


# --------------------------------------------------------------------------- pandas side


def clause_mask(clause: Clause, series: pd.Series, dtype: str) -> pd.Series:
    """Rows that satisfy one filter condition (SQL semantics: NULL never matches)."""
    if clause.op == "is_null":
        return series.isna()
    if clause.op == "not_null":
        return series.notna()
    value = clause.value
    if dtype == "date":
        series = pd.to_datetime(series)
        value = tuple(pd.Timestamp(v) for v in value) if clause.op == "in" else pd.Timestamp(value)
    if clause.op == "in":
        return series.isin(list(value))
    tests = {
        "=": lambda: series == value,
        "!=": lambda: series.notna() & (series != value),
        ">": lambda: series > value,
        ">=": lambda: series >= value,
        "<": lambda: series < value,
        "<=": lambda: series <= value,
    }
    return tests[clause.op]().fillna(False).astype(bool)


def row_mask(spec: Spec, frame: pd.DataFrame, ctx: Context) -> pd.Series:
    """Rows selected by the filter of a plain (non-ratio) spec."""
    mask = pd.Series(True, index=frame.index)
    for clause in spec.filters:
        mask &= clause_mask(clause, frame[clause.column], ctx.columns[clause.column].dtype)
    return mask


def numbers(series: pd.Series) -> pd.Series:
    """A column as floats without missing values."""
    return pd.to_numeric(series, errors="coerce").astype(float).dropna()


def reference_value(spec: Spec, frame: pd.DataFrame, ctx: Context) -> float | None:
    """The metric computed with pandas only (independent of the SQL compiler)."""
    if spec.agg == "ratio":
        top = reference_value(spec.numerator, frame, ctx)
        bottom = reference_value(spec.denominator, frame, ctx)
        if top is None or bottom is None or bottom == 0:
            return None
        return top / bottom * (100.0 if spec.times100 else 1.0)
    part = frame[row_mask(spec, frame, ctx)]
    if spec.agg == "count":
        return float(len(part) if not spec.columns else part[spec.columns[0]].notna().sum())
    if spec.agg == "count_distinct":
        return float(part[spec.columns[0]].nunique())
    if spec.agg == "avg_days_between":
        first = pd.to_datetime(part[spec.columns[0]]).dt.normalize()
        second = pd.to_datetime(part[spec.columns[1]]).dt.normalize()
        days = (second - first).dt.days.dropna()
        return float(days.mean()) if len(days) else None
    return numeric_reference(spec, numbers(part[spec.columns[0]]))


def numeric_reference(spec: Spec, values: pd.Series) -> float | None:
    """Aggregate of a numeric series, ``None`` where SQL would return NULL."""
    if values.empty:
        return None
    if spec.agg == "stddev":
        return float(values.std(ddof=1)) if len(values) > 1 else None
    if spec.agg == "quantile":
        return float(values.quantile(spec.q))
    functions = {
        "sum": values.sum,
        "avg": values.mean,
        "min": values.min,
        "max": values.max,
        "median": values.median,
    }
    return float(functions[spec.agg]())


# --------------------------------------------------------------------------- checks


def column_problem(spec: Spec, ctx: Context) -> str | None:
    """Static checks on the profile: too many nulls, constant column."""
    if spec.agg == "ratio":
        return column_problem(spec.numerator, ctx) or column_problem(spec.denominator, ctx)
    for name in spec.columns:
        info = ctx.columns[name]
        if spec.agg != "count" and info.null_pct > MAX_NULL_PCT:
            return f"{name} is more than {MAX_NULL_PCT:.0f}% empty"
        if spec.agg in NUMERIC_AGGS and info.distinct < 2:
            return f"{name} is constant"
    return None


def selects_rows(spec: Spec, frame: pd.DataFrame, ctx: Context) -> bool:
    """False when a filter matches no row (the metric would be a constant zero or NULL)."""
    if spec.agg == "ratio":
        return selects_rows(spec.numerator, frame, ctx) and selects_rows(
            spec.denominator, frame, ctx
        )
    return bool(row_mask(spec, frame, ctx).any())


def run_sql(
    con: duckdb.DuckDBPyConnection, spec: Spec, ctx: Context, settings: Settings
) -> float | None:
    """Execute the compiled expression through the normal query guard."""
    result = execute_query(con, f"SELECT {compile_sql(spec, ctx)} AS value FROM data", settings)
    value = result.rows[0][0]
    return None if value is None else float(value)


def same_number(actual: float, expected: float) -> bool:
    """Relative comparison with the parity tolerance."""
    return math.isclose(actual, expected, rel_tol=PARITY_TOLERANCE, abs_tol=1e-9)


def validate_spec(
    spec: Spec,
    ctx: Context,
    con: duckdb.DuckDBPyConnection,
    frame: pd.DataFrame,
    settings: Settings,
) -> Validation:
    """Run all checks. Never raises: every problem becomes ``Validation(ok=False, ...)``."""
    try:
        return _validate(spec, ctx, con, frame, settings)
    except (DslError, UserFacingError, duckdb.Error) as exc:
        return Validation(False, f"{type(exc).__name__}: {str(exc)[:120]}")
    except Exception as exc:  # noqa: BLE001 - one bad metric must never stop the autopilot
        logger.warning("autopilot_validation_error type=%s", type(exc).__name__)
        return Validation(False, f"unexpected {type(exc).__name__}")


def _validate(
    spec: Spec,
    ctx: Context,
    con: duckdb.DuckDBPyConnection,
    frame: pd.DataFrame,
    settings: Settings,
) -> Validation:
    problem = column_problem(spec, ctx)
    if problem:
        return Validation(False, problem)
    if not selects_rows(spec, frame, ctx):
        return Validation(False, "filter matches no rows")
    actual = run_sql(con, spec, ctx, settings)
    if actual is None or not math.isfinite(actual):
        return Validation(False, "no finite value")
    if spec.agg == "avg_days_between" and actual < 0:
        return Validation(False, "the second date is before the first one")
    expected = reference_value(spec, frame, ctx)
    if expected is None or not math.isfinite(expected):
        return Validation(False, "no reference value (empty or zero denominator)")
    if not same_number(actual, expected):
        return Validation(False, f"parity mismatch {actual!r} vs {expected!r}")
    return Validation(True, value=actual)
