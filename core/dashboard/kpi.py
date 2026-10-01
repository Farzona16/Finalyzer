"""KPI cards: each metric over the selected range and over the previous period of equal length."""

from __future__ import annotations

import logging
from datetime import date

import duckdb
from pydantic import BaseModel

from core.config import Settings, get_settings
from core.dashboard.filters import where_sql
from core.errors import UserFacingError
from core.ingest.profiler import quote_identifier
from core.metrics.builder import metric_sql
from core.metrics.timeintel import previous_range
from core.models import DatasetModel, FilterSpec, Metric
from core.query.executor import run_with_timeout

logger = logging.getLogger("finalyzer.kpi")

MAX_KPIS = 6
NEUTRAL_DELTA = 0.05  # changes smaller than this (percent or points) are shown as neutral


class KpiValue(BaseModel):
    """One KPI card. ``delta`` is a percent change, or points for percent metrics."""

    metric_id: str
    label: str
    unit: str
    value: float | None
    previous: float | None
    delta: float | None
    delta_kind: str | None  # "pct", "pp" or None when there is nothing to compare with
    tone: str  # "good", "bad" or "neutral"
    display: str
    delta_display: str


def compact_number(value: float) -> str:
    """1234 -> 1,234; 1234567 -> 1.23M."""
    magnitude = abs(value)
    for limit, suffix in ((1e12, "T"), (1e9, "B"), (1e6, "M")):
        if magnitude >= limit:
            return f"{value / limit:,.2f}{suffix}"
    if magnitude >= 100:
        return f"{value:,.0f}"
    return f"{value:,.2f}"


def format_value(value: float | None, unit: str) -> str:
    """Readable text for a metric value; ``n/a`` when there is none."""
    if value is None:
        return "n/a"
    if unit == "percent":
        return f"{value:.1f}%"
    if unit == "ratio":
        return f"{value:.2f}"
    if unit == "count":
        return f"{value:,.0f}" if abs(value) < 1e6 else compact_number(value)
    return compact_number(value)


def compute_delta(
    current: float | None, previous: float | None, unit: str
) -> tuple[float | None, str | None]:
    """Percent change (points for percent metrics) or ``(None, None)`` when not comparable."""
    if current is None or previous is None:
        return None, None
    if unit == "percent":
        return current - previous, "pp"
    if previous == 0:
        return None, None
    return (current - previous) / abs(previous) * 100.0, "pct"


def delta_tone(delta: float | None, direction: str) -> str:
    """``good`` or ``bad`` from the sign of the change and the metric's good direction."""
    if delta is None or abs(delta) < NEUTRAL_DELTA or direction == "neutral":
        return "neutral"
    improving = delta > 0 if direction == "up" else delta < 0
    return "good" if improving else "bad"


def format_delta(delta: float | None, kind: str | None) -> str:
    """Text such as ``+12.3%`` or ``-1.5 pts``; ``n/a`` when there is no comparison."""
    if delta is None or kind is None:
        return "n/a"
    return f"{delta:+.1f}%" if kind == "pct" else f"{delta:+.1f} pts"


def to_float(value: object) -> float | None:
    """A DuckDB value as float (None stays None)."""
    return None if value is None else float(value)


def aggregate_row(
    con: duckdb.DuckDBPyConnection, metrics: list[Metric], roles: dict[str, str],
    source: str, settings: Settings,
) -> list[float | None]:
    """Values of several metrics from one scan of ``source`` (a table or ``table WHERE ...``)."""
    exprs = ", ".join(f"{metric_sql(m, roles)} AS m{i}" for i, m in enumerate(metrics))
    _, rows = run_with_timeout(con, f"SELECT {exprs} FROM {source}", settings.query_timeout_s)
    return [to_float(v) for v in rows[0]]


def safe_aggregate(
    con: duckdb.DuckDBPyConnection, metrics: list[Metric], roles: dict[str, str],
    source: str, settings: Settings,
) -> list[float | None]:
    """Like ``aggregate_row`` but a broken metric only loses its own value."""
    try:
        return aggregate_row(con, metrics, roles, source, settings)
    except UserFacingError:
        raise
    except Exception:  # noqa: BLE001 - fall back to one query per metric
        values: list[float | None] = []
        for metric in metrics:
            try:
                values.append(aggregate_row(con, [metric], roles, source, settings)[0])
            except UserFacingError:
                raise
            except Exception as exc:  # noqa: BLE001
                logger.warning("kpi_failed metric=%s type=%s", metric.id, type(exc).__name__)
                values.append(None)
        return values


def selected_range(
    con: duckdb.DuckDBPyConnection, model: DatasetModel, spec: FilterSpec
) -> tuple[date, date] | None:
    """The date range the KPIs describe: the slicer range or the span of the filtered data."""
    if spec.date_range:
        return spec.date_range
    column = model.roles.get("date")
    if not column:
        return None
    col = quote_identifier(column)
    low, high = con.execute(f"SELECT MIN({col}), MAX({col}) FROM data").fetchone()
    if low is None or high is None:
        return None
    return (low, high) if isinstance(low, date) else None


def build_kpi(metric: Metric, current: float | None, previous: float | None) -> KpiValue:
    """Assemble the card for one metric from its two values."""
    delta, kind = compute_delta(current, previous, metric.unit)
    return KpiValue(
        metric_id=metric.id,
        label=metric.label,
        unit=metric.unit,
        value=current,
        previous=previous,
        delta=delta,
        delta_kind=kind,
        tone=delta_tone(delta, metric.good_direction),
        display=format_value(current, metric.unit),
        delta_display=format_delta(delta, kind),
    )


def compute_kpis(
    con: duckdb.DuckDBPyConnection,
    model: DatasetModel,
    metrics: list[Metric],
    spec: FilterSpec,
    settings: Settings | None = None,
    limit: int = MAX_KPIS,
) -> list[KpiValue]:
    """KPI cards for the first ``limit`` metrics (the caller passes the primary metric first)."""
    settings = settings or get_settings()
    chosen = metrics[:limit]
    if not chosen:
        return []
    roles = model.roles
    current = safe_aggregate(con, chosen, roles, "data", settings)
    previous: list[float | None] = [None] * len(chosen)
    span = selected_range(con, model, spec)
    if span is not None:
        before = previous_range(span[0], span[1])
        source = f"base {where_sql(spec, model, date_override=before)}"
        if has_rows(con, source):
            previous = safe_aggregate(con, chosen, roles, source, settings)
    return [build_kpi(m, c, p) for m, c, p in zip(chosen, current, previous, strict=True)]


def has_rows(con: duckdb.DuckDBPyConnection, source: str) -> bool:
    """False when a period is empty (a count metric would say 0 instead of ``n/a``)."""
    return con.execute(f"SELECT EXISTS (SELECT 1 FROM {source})").fetchone()[0]
