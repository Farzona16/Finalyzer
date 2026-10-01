"""Deterministic SQL for a filled route. The LLM never writes this SQL."""

from __future__ import annotations

from core.ingest.profiler import quote_identifier
from core.metrics.builder import metric_sql
from core.metrics.timeintel import date_column, date_literal, series, share_of_total, yoy
from core.nlu.periods import Period
from core.nlu.router import Route

GRAIN = {"trend": "month", "daily": "day", "mom": "month", "growth": "month"}


def ident(name: str) -> str:
    """A quoted SQL identifier."""
    return quote_identifier(name)


def sql_str(value: str) -> str:
    """A quoted SQL string literal."""
    return "'" + value.replace("'", "''") + "'"


def expr_of(route: Route) -> str:
    """The metric expression, aliased to the metric id."""
    assert route.metric is not None
    return f"{metric_sql(route.metric, _roles(route))} AS {ident(route.metric.id)}"


def _roles(route: Route) -> dict[str, str]:
    return route.metric.spec.get("roles", {}) if route.metric and route.metric.spec else {}


def compile_sql(route: Route, roles: dict[str, str]) -> str | None:
    """DuckDB SQL for ``route``, or None when the intent cannot be compiled."""
    route = _with_roles(route, roles)
    compiler = COMPILERS.get(route.intent, compile_total)
    try:
        return compiler(route, roles)
    except Exception:  # noqa: BLE001 - a failed compile falls through to the LLM path
        return None


def _with_roles(route: Route, roles: dict[str, str]) -> Route:
    """Stash roles on the metric so helpers can read them without extra arguments."""
    if route.metric is not None:
        route.metric = route.metric.model_copy(
            update={"spec": {**(route.metric.spec or {}), "roles": roles}}
        )
    return route


def where_clause(route: Route, roles: dict[str, str], extra: str = "") -> str:
    """WHERE for the bound period and category filter."""
    parts: list[str] = []
    period: Period | None = route.period
    if (
        period is not None
        and period.start is not None
        and period.end is not None
        and "date" in roles
    ):
        day = date_column(roles)
        parts.append(
            f"{day} >= {date_literal(period.start)} AND {day} < {date_literal(period.end)}"
        )
    if route.value is not None:
        column, value = route.value
        parts.append(f"{ident(column)} = {sql_str(value)}")
    if extra:
        parts.append(extra)
    return (" WHERE " + " AND ".join(parts)) if parts else ""


def compile_total(route: Route, roles: dict[str, str]) -> str:
    assert route.metric is not None
    return f"SELECT {expr_of(route)} FROM data{where_clause(route, roles)}"


def compile_grouped(route: Route, roles: dict[str, str], order: str, limit: int | None) -> str:
    assert route.metric is not None and route.dimension
    dim = ident(route.dimension)
    extra = f"{dim} IS NOT NULL"
    grouped = where_clause(route, roles, extra)
    sql = (
        f"SELECT {dim} AS {ident(route.dimension)}, {expr_of(route)} FROM data"
        f"{grouped} GROUP BY 1 ORDER BY 2 {order} NULLS LAST"
    )
    return sql if limit is None else f"{sql} LIMIT {int(limit)}"


def compile_by_dimension(route: Route, roles: dict[str, str]) -> str:
    return compile_grouped(route, roles, "DESC", None)


def compile_top_n(route: Route, roles: dict[str, str]) -> str:
    return compile_grouped(route, roles, "DESC", route.n or 5)


def compile_bottom_n(route: Route, roles: dict[str, str]) -> str:
    return compile_grouped(route, roles, "ASC", route.n or 5)


def compile_best(route: Route, roles: dict[str, str]) -> str:
    return compile_grouped(route, roles, "DESC", 1)


def compile_worst(route: Route, roles: dict[str, str]) -> str:
    return compile_grouped(route, roles, "ASC", 1)


def compile_trend(route: Route, roles: dict[str, str]) -> str:
    assert route.metric is not None
    grain = GRAIN.get(route.intent, "month")
    return series(route.metric, roles, grain)


def compile_daily(route: Route, roles: dict[str, str]) -> str:
    return compile_trend(route, roles)


def compile_yoy(route: Route, roles: dict[str, str]) -> str:
    assert route.metric is not None
    return yoy(route.metric, roles, "month")


def compile_mom(route: Route, roles: dict[str, str]) -> str:
    assert route.metric is not None
    inner = series(route.metric, roles, "month")
    return (
        f"WITH monthly AS ({inner}) SELECT period, value, "
        "100.0 * (value - LAG(value) OVER (ORDER BY period)) "
        "/ NULLIF(LAG(value) OVER (ORDER BY period), 0) AS mom_pct FROM monthly ORDER BY period"
    )


def compile_share(route: Route, roles: dict[str, str]) -> str:
    assert route.metric is not None
    if route.dimension:
        return share_of_total(route.metric, roles, route.dimension)
    return compile_total(route, roles)


def compile_last_month(route: Route, roles: dict[str, str]) -> str:
    return compile_total(route, roles)


def compile_this_year(route: Route, roles: dict[str, str]) -> str:
    return compile_total(route, roles)


def compile_period_value(route: Route, roles: dict[str, str]) -> str:
    return compile_total(route, roles)


def compile_filter_value(route: Route, roles: dict[str, str]) -> str:
    return compile_total(route, roles)


def compile_count(route: Route, roles: dict[str, str]) -> str:
    return f"SELECT COUNT(*) AS row_count FROM data{where_clause(route, roles)}"


def compile_distinct(route: Route, roles: dict[str, str]) -> str:
    column = route.dimension or route.column
    assert column
    return f"SELECT COUNT(DISTINCT {ident(column)}) AS n FROM data{where_clause(route, roles)}"


def compile_average(route: Route, roles: dict[str, str]) -> str:
    if route.column:
        return (
            f"SELECT AVG({ident(route.column)}) AS avg_value FROM data{where_clause(route, roles)}"
        )
    return compile_total(route, roles)


def compile_minimum(route: Route, roles: dict[str, str]) -> str:
    assert route.column
    return f"SELECT MIN({ident(route.column)}) AS min_value FROM data{where_clause(route, roles)}"


def compile_maximum(route: Route, roles: dict[str, str]) -> str:
    assert route.column
    return f"SELECT MAX({ident(route.column)}) AS max_value FROM data{where_clause(route, roles)}"


def compile_median(route: Route, roles: dict[str, str]) -> str:
    assert route.column
    clause = where_clause(route, roles)
    return f"SELECT median({ident(route.column)}) AS median_value FROM data{clause}"


def compile_distribution(route: Route, roles: dict[str, str]) -> str:
    assert route.column
    col = ident(route.column)
    return (
        f"SELECT MIN({col}) AS min_value, AVG({col}) AS avg_value, "
        f"MAX({col}) AS max_value, STDDEV_SAMP({col}) AS stddev FROM data"
        f"{where_clause(route, roles)}"
    )


def compile_missing(route: Route, roles: dict[str, str]) -> str:
    assert route.column
    col = ident(route.column)
    return (
        f"SELECT COUNT(*) FILTER (WHERE {col} IS NULL) AS missing, "
        f"COUNT(*) AS rows FROM data{where_clause(route, roles)}"
    )


def compile_correlation(route: Route, roles: dict[str, str]) -> str:
    assert route.column and route.column_b
    a, b = ident(route.column), ident(route.column_b)
    return f"SELECT corr({a}, {b}) AS pearson FROM data WHERE {a} IS NOT NULL AND {b} IS NOT NULL"


def compile_ratio(route: Route, roles: dict[str, str]) -> str:
    return compile_correlation(route, roles)


def compile_funnel(route: Route, roles: dict[str, str]) -> str:
    column = roles.get("stage") or route.dimension or "stage"
    return (
        f"SELECT {ident(column)} AS stage, COUNT(*) AS deals FROM data "
        f"WHERE {ident(column)} IS NOT NULL GROUP BY 1 ORDER BY 2 DESC"
    )


def compile_stalled(route: Route, roles: dict[str, str]) -> str:
    day = date_column(roles) if "date" in roles else ident("created_date")
    status = ident(roles["status"]) if "status" in roles else ident("status")
    amount = ident(roles["amount"]) if "amount" in roles else ident("amount")
    return (
        f"SELECT COUNT(*) AS deals, SUM({amount}) AS value FROM data "
        f"WHERE {status} = 'open' AND {day} < (SELECT MAX({day}) FROM data) - INTERVAL 90 DAY"
    )


def compile_why_drop(route: Route, roles: dict[str, str]) -> str:
    dimension = route.dimension or (route.value[0] if route.value else None)
    if not dimension:
        raise ValueError("why_drop needs a dimension")
    return compile_grouped(route, roles, "ASC", 8)


def compile_having_gt(route: Route, roles: dict[str, str]) -> str:
    assert route.metric is not None and route.dimension and route.n is not None
    dim = ident(route.dimension)
    metric = metric_sql(route.metric, roles)
    return (
        f"SELECT {dim} AS {ident(route.dimension)}, {metric} AS {ident(route.metric.id)} "
        f"FROM data WHERE {dim} IS NOT NULL GROUP BY 1 HAVING {metric} > {int(route.n)} "
        f"ORDER BY 2 DESC"
    )


def compile_rank(route: Route, roles: dict[str, str]) -> str:
    assert route.metric is not None and route.dimension
    dim = ident(route.dimension)
    metric = metric_sql(route.metric, roles)
    return (
        f"SELECT {dim} AS {ident(route.dimension)}, {metric} AS {ident(route.metric.id)}, "
        f"RANK() OVER (ORDER BY {metric} DESC) AS rank FROM data "
        f"WHERE {dim} IS NOT NULL GROUP BY 1 ORDER BY rank"
    )


def compile_list_values(route: Route, roles: dict[str, str]) -> str:
    assert route.dimension
    dim = ident(route.dimension)
    return (
        f"SELECT DISTINCT {dim} AS {ident(route.dimension)} FROM data "
        f"WHERE {dim} IS NOT NULL ORDER BY 1"
    )


def compile_compare_segments(route: Route, roles: dict[str, str]) -> str:
    return compile_by_dimension(route, roles)


def compile_growth(route: Route, roles: dict[str, str]) -> str:
    return compile_mom(route, roles)


def compile_win_rate(route: Route, roles: dict[str, str]) -> str:
    return compile_total(route, roles)


def compile_pipeline(route: Route, roles: dict[str, str]) -> str:
    return compile_total(route, roles)


COMPILERS = {
    "total": compile_total,
    "by_dimension": compile_by_dimension,
    "top_n": compile_top_n,
    "bottom_n": compile_bottom_n,
    "best": compile_best,
    "worst": compile_worst,
    "trend": compile_trend,
    "daily": compile_daily,
    "yoy": compile_yoy,
    "mom": compile_mom,
    "share": compile_share,
    "last_month": compile_last_month,
    "this_year": compile_this_year,
    "period_value": compile_period_value,
    "filter_value": compile_filter_value,
    "count": compile_count,
    "distinct": compile_distinct,
    "average": compile_average,
    "minimum": compile_minimum,
    "maximum": compile_maximum,
    "median": compile_median,
    "distribution": compile_distribution,
    "missing": compile_missing,
    "correlation": compile_correlation,
    "ratio": compile_ratio,
    "funnel": compile_funnel,
    "stalled": compile_stalled,
    "why_drop": compile_why_drop,
    "having_gt": compile_having_gt,
    "rank": compile_rank,
    "list_values": compile_list_values,
    "compare_segments": compile_compare_segments,
    "growth": compile_growth,
    "win_rate": compile_win_rate,
    "pipeline": compile_pipeline,
}
