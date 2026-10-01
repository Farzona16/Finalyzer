"""The restricted metric language (spec 6.1, step 3).

A model (or the heuristics) describes a metric as a small JSON object. This module checks the
object against the real columns of the dataset and turns it into one SQL aggregate expression.
**The model never writes SQL**: every identifier in the output is an existing, quoted column
name, every literal is a checked number, date or known category value, and every function name
comes from a fixed table below.

Anything that does not fit raises ``DslError``; callers drop such metrics silently.
"""

from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from core.ingest.profiler import quote_identifier
from core.models import ColumnInfo, DatasetModel

AGGS = (
    "sum",
    "avg",
    "count",
    "count_distinct",
    "min",
    "max",
    "median",
    "stddev",
    "quantile",
    "avg_days_between",
    "ratio",
)
OPS = ("=", "!=", "in", ">", ">=", "<", "<=", "is_null", "not_null")
UNITS = ("currency", "count", "percent", "ratio")
DIRECTIONS = ("up", "down", "neutral")
NUMERIC_AGGS = ("sum", "avg", "min", "max", "median", "stddev", "quantile")
ORDER_OPS = (">", ">=", "<", "<=")
MAX_FILTERS = 5
MAX_IN_VALUES = 20
MAX_TEXT = 100
MAX_NUMBER = 1e15
ID_RE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
ID_NAME_RE = re.compile(r"(^|_)(id|uuid|guid)$|^id_", re.IGNORECASE)
ID_ROLES = ("order_id", "customer_id")
BAD_TEXT_RE = re.compile(r"[\x00-\x1f\x7f{}\\]")
SUM_UNITS_BY_ROLE = {"amount": "currency", "cost": "currency", "target": "currency"}

Scalar = str | int | float | bool


class DslError(ValueError):
    """The metric object is not valid for this dataset."""


@dataclass(frozen=True)
class Clause:
    """One condition of a filter: ``column op value`` (value is a tuple for ``in``)."""

    column: str
    op: str
    value: Any = None


@dataclass(frozen=True)
class Spec:
    """A checked metric description. Build it with ``parse_spec``."""

    agg: str
    columns: tuple[str, ...] = ()
    filters: tuple[Clause, ...] = ()
    q: float | None = None
    numerator: Spec | None = None
    denominator: Spec | None = None
    times100: bool = False
    id: str = ""
    unit: str = "count"
    good_direction: str = "neutral"
    labels: dict[str, str] = field(default_factory=dict, compare=False, hash=False)


@dataclass(frozen=True)
class Context:
    """What the parser needs to know about the dataset."""

    columns: dict[str, ColumnInfo]
    roles: dict[str, str]
    row_count: int

    @classmethod
    def from_model(cls, model: DatasetModel) -> Context:
        return cls({c.name: c for c in model.columns}, dict(model.roles), model.row_count)


def is_id_like(column: ColumnInfo, ctx: Context) -> bool:
    """Identifier columns (never summed or averaged): by name, by role or a dense 1..N key."""
    if ID_NAME_RE.search(column.name) or ctx.roles.get("order_id") == column.name:
        return True
    if ctx.roles.get("customer_id") == column.name:
        return True
    if column.dtype != "integer" or ctx.row_count < 50:
        return False
    low, high = column.min_value, column.max_value
    if not isinstance(low, int | float) or not isinstance(high, int | float):
        return False
    unique = column.distinct >= 0.99 * ctx.row_count * (1 - column.null_pct / 100)
    return unique and (high - low + 1) <= 1.5 * column.distinct


def _column(name: Any, ctx: Context) -> ColumnInfo:
    if not isinstance(name, str) or name not in ctx.columns:
        raise DslError("unknown column")
    info = ctx.columns[name]
    if info.is_pii or BAD_TEXT_RE.search(name) or len(name) > 128:
        raise DslError("column not allowed")
    return info


def _number(value: Any) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float) or not math.isfinite(value):
        raise DslError("not a finite number")
    if abs(value) > MAX_NUMBER:
        raise DslError("number too large")
    return float(value) if isinstance(value, float) else value


def _date(value: Any) -> str:
    if not isinstance(value, str):
        raise DslError("not a date")
    try:
        return date.fromisoformat(value[:10]).isoformat()
    except ValueError as exc:
        raise DslError("not a date") from exc


def _category(value: Any, info: ColumnInfo) -> str:
    if not isinstance(value, str) or BAD_TEXT_RE.search(value) or len(value) > MAX_TEXT:
        raise DslError("bad text value")
    known = info.categories
    if not known:
        raise DslError("category values are unknown")
    for item in known:
        if item == value:
            return item
    lowered = [item for item in known if item.casefold() == value.casefold()]
    if len(lowered) == 1:
        return lowered[0]
    raise DslError("value is not a known category")


def _scalar(value: Any, info: ColumnInfo) -> Scalar:
    """Check one filter value against the type of its column."""
    if info.dtype in ("integer", "float"):
        return _number(value)
    if info.dtype == "date":
        return _date(value)
    if info.dtype == "boolean":
        if isinstance(value, bool):
            return value
        raise DslError("not a boolean")
    return _category(value, info)


def _clause(raw: Any, ctx: Context) -> Clause:
    if not isinstance(raw, dict):
        raise DslError("filter must be an object")
    info = _column(raw.get("column"), ctx)
    op = raw.get("op")
    if op not in OPS:
        raise DslError("unknown operator")
    value = raw.get("value")
    if op in ("is_null", "not_null"):
        return Clause(info.name, op)
    if op == "in":
        if not isinstance(value, list) or not 1 <= len(value) <= MAX_IN_VALUES:
            raise DslError("'in' needs a short list")
        return Clause(info.name, op, tuple(_scalar(v, info) for v in value))
    if op in ORDER_OPS and info.dtype not in ("integer", "float", "date"):
        raise DslError("comparison needs a number or date column")
    return Clause(info.name, op, _scalar(value, info))


def _filters(raw: Any, ctx: Context) -> tuple[Clause, ...]:
    if raw in (None, []):
        return ()
    if not isinstance(raw, list) or len(raw) > MAX_FILTERS:
        raise DslError("too many filters")
    return tuple(_clause(item, ctx) for item in raw)


def _columns(raw: Any, agg: str, ctx: Context) -> tuple[str, ...]:
    names = raw if isinstance(raw, list) else ([] if raw is None else [raw])
    infos = [_column(name, ctx) for name in names]
    wanted = {"count": (0, 1), "count_distinct": (1, 1), "avg_days_between": (2, 2)}
    low, high = wanted.get(agg, (1, 1))
    if not low <= len(infos) <= high:
        raise DslError(f"{agg} needs {low} to {high} columns")
    for info in infos:
        _check_column_type(agg, info, ctx)
    if len({i.name for i in infos}) != len(infos):
        raise DslError("columns must differ")
    return tuple(i.name for i in infos)


def _check_column_type(agg: str, info: ColumnInfo, ctx: Context) -> None:
    if agg in NUMERIC_AGGS:
        if info.dtype not in ("integer", "float"):
            raise DslError(f"{agg} needs a numeric column")
        if is_id_like(info, ctx):
            raise DslError("identifier columns cannot be aggregated")
    if agg == "avg_days_between" and info.dtype != "date":
        raise DslError("avg_days_between needs date columns")


def _text(value: Any) -> str:
    if not isinstance(value, str) or BAD_TEXT_RE.search(value):
        raise DslError("bad text")
    return value.strip()[:MAX_TEXT]


def _labels(raw: Any) -> dict[str, str]:
    if raw is None:
        return {}
    if isinstance(raw, str):
        return {"en": _text(raw)}
    if not isinstance(raw, dict):
        raise DslError("bad label")
    return {k: _text(v) for k, v in raw.items() if k in ("en", "uz") and _text(v)}


def parse_spec(raw: Any, ctx: Context, *, nested: bool = False) -> Spec:
    """Check a metric object and return it as a ``Spec``. Raises ``DslError`` only."""
    if not isinstance(raw, dict):
        raise DslError("metric must be an object")
    try:
        return _parse(raw, ctx, nested)
    except DslError:
        raise
    except (TypeError, ValueError, KeyError, AttributeError, OverflowError) as exc:
        raise DslError(f"invalid metric: {type(exc).__name__}") from exc


def _parse(raw: dict[str, Any], ctx: Context, nested: bool) -> Spec:
    agg = raw.get("agg")
    if agg not in AGGS:
        raise DslError("unknown aggregation")
    metric_id = raw.get("id", "")
    if metric_id != "" and (not isinstance(metric_id, str) or not ID_RE.match(metric_id)):
        raise DslError("bad id")
    unit = raw.get("unit", "count")
    direction = raw.get("good_direction", "neutral")
    if unit not in UNITS or direction not in DIRECTIONS:
        raise DslError("bad unit or direction")
    common = {
        "id": metric_id,
        "unit": unit,
        "good_direction": direction,
        "labels": _labels(raw.get("label")),
    }
    if agg == "ratio":
        return _parse_ratio(raw, ctx, nested, common)
    q = _quantile(raw.get("q")) if agg == "quantile" else None
    if agg != "quantile" and raw.get("q") is not None:
        raise DslError("q is only for quantile")
    columns = _columns(raw.get("columns"), agg, ctx)
    return Spec(agg=agg, columns=columns, filters=_filters(raw.get("filter"), ctx), q=q, **common)


def _quantile(value: Any) -> float:
    q = _number(value)
    if not 0.0 < q < 1.0:
        raise DslError("q must be between 0 and 1")
    return float(q)


def _parse_ratio(raw: dict[str, Any], ctx: Context, nested: bool, common: dict[str, Any]) -> Spec:
    if nested:
        raise DslError("ratios cannot be nested")
    parts = [parse_spec(raw.get(k), ctx, nested=True) for k in ("numerator", "denominator")]
    times100 = raw.get("times100", False)
    if not isinstance(times100, bool):
        raise DslError("times100 must be true or false")
    return Spec(agg="ratio", numerator=parts[0], denominator=parts[1], times100=times100, **common)


# --------------------------------------------------------------------------- SQL


def sql_literal(value: Any, dtype: str) -> str:
    """A checked value as a SQL literal (the parser already validated it)."""
    if dtype == "date":
        return f"DATE '{_date(value)}'"
    if dtype == "boolean":
        return "TRUE" if value else "FALSE"
    if dtype in ("integer", "float"):
        return repr(_number(value))
    return "'" + str(value).replace("'", "''") + "'"


def _clause_sql(clause: Clause, ctx: Context) -> str:
    column = quote_identifier(clause.column)
    dtype = ctx.columns[clause.column].dtype
    if clause.op == "is_null":
        return f"{column} IS NULL"
    if clause.op == "not_null":
        return f"{column} IS NOT NULL"
    if clause.op == "in":
        values = ", ".join(sql_literal(v, dtype) for v in clause.value)
        return f"{column} IN ({values})"
    operator = "<>" if clause.op == "!=" else clause.op
    return f"{column} {operator} {sql_literal(clause.value, dtype)}"


def where_sql(spec: Spec, ctx: Context) -> str:
    """The ``WHERE`` condition of a spec (empty text when it has no filter)."""
    return " AND ".join(_clause_sql(c, ctx) for c in spec.filters)


def _base_sql(spec: Spec) -> str:
    cols = [quote_identifier(c) for c in spec.columns]
    simple = {
        "sum": "SUM({0})",
        "avg": "AVG({0})",
        "min": "MIN({0})",
        "max": "MAX({0})",
        "median": "MEDIAN({0})",
        "stddev": "STDDEV_SAMP({0})",
        "count_distinct": "COUNT(DISTINCT {0})",
    }
    if spec.agg in simple:
        return simple[spec.agg].format(*cols)
    if spec.agg == "count":
        return f"COUNT({cols[0]})" if cols else "COUNT(*)"
    if spec.agg == "quantile":
        return f"QUANTILE_CONT({cols[0]}, {spec.q!r})"
    return f"AVG(date_diff('day', CAST({cols[0]} AS DATE), CAST({cols[1]} AS DATE)))"


def compile_sql(spec: Spec, ctx: Context) -> str:
    """One SQL aggregate expression over the ``data`` view."""
    if spec.agg == "ratio":
        top = compile_sql(spec.numerator, ctx)
        bottom = compile_sql(spec.denominator, ctx)
        scale = "100.0 * " if spec.times100 else ""
        return f"{scale}({top}) / NULLIF(({bottom}), 0)"
    base = _base_sql(spec)
    where = where_sql(spec, ctx)
    return f"{base} FILTER (WHERE {where})" if where else base


# --------------------------------------------------------------------------- identity


def to_dict(spec: Spec) -> dict[str, Any]:
    """The JSON form of a checked spec (what is stored and shown in the Expert view)."""
    if spec.agg == "ratio":
        data: dict[str, Any] = {
            "agg": "ratio",
            "numerator": to_dict(spec.numerator),
            "denominator": to_dict(spec.denominator),
            "times100": spec.times100,
        }
    else:
        data = {"agg": spec.agg, "columns": list(spec.columns)}
        if spec.q is not None:
            data["q"] = spec.q
        if spec.filters:
            data["filter"] = [_clause_dict(c) for c in spec.filters]
    data.update(id=spec.id, unit=spec.unit, good_direction=spec.good_direction)
    if spec.labels:
        data["label"] = dict(spec.labels)
    return data


def _clause_dict(clause: Clause) -> dict[str, Any]:
    data: dict[str, Any] = {"column": clause.column, "op": clause.op}
    if clause.op not in ("is_null", "not_null"):
        data["value"] = list(clause.value) if clause.op == "in" else clause.value
    return data


def canonical(spec: Spec) -> str:
    """A text that is equal for metrics that mean the same (ids, labels and units ignored)."""
    if spec.agg == "ratio":
        body: Any = ["ratio", canonical(spec.numerator), canonical(spec.denominator), spec.times100]
    else:
        clauses = sorted(json.dumps(_clause_dict(c), sort_keys=True) for c in spec.filters)
        columns = sorted(spec.columns) if spec.agg == "count_distinct" else list(spec.columns)
        body = [spec.agg, columns, spec.q, clauses]
    return json.dumps(body, sort_keys=True)
