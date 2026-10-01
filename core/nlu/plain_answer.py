"""Plain-language Ask answers from frames (spec 7.5). Numbers come only from the query result."""

from __future__ import annotations

import json
from pathlib import Path

from pydantic import BaseModel, Field

from core.i18n.formats import EMPTY, format_compact, format_currency, format_decimal, format_integer
from core.models import Metric, QueryResult
from core.nlu.router import Route
from core.query.charts import ChartSpec, choose_chart
from core.query.nl2sql import format_number

FRAMES = json.loads(Path(__file__).with_name("answer_frames.json").read_text(encoding="utf-8"))


class ClarificationOption(BaseModel):
    """One button on a clarifying question."""

    label: str
    append: str


class PlainAnswer(BaseModel):
    """What the Ask page shows for one question."""

    status: str
    headline: str
    explanation: str
    so_what: str = ""
    next_step: str = ""
    details: str = ""
    badge: str = ""
    verified: bool = False
    used_filters: list[str] = Field(default_factory=list)
    period: str = ""
    chart: ChartSpec | None = None
    table: QueryResult | None = None
    sql: str | None = None
    options: list[ClarificationOption] = Field(default_factory=list)
    followups: list[str] = Field(default_factory=list)
    stage: str = "done"


def frame(locale: str, key: str, **values: object) -> str:
    """One sentence of the answer catalog."""
    catalog = FRAMES.get(locale, FRAMES["uz"])
    return str(catalog[key]).format(**values)


def format_cell(value: object, metric: Metric | None, locale: str, currency: str) -> str:
    """A single KPI cell with the metric's unit."""
    if value is None:
        return EMPTY
    if metric is None or not isinstance(value, int | float) or isinstance(value, bool):
        return format_number(value)
    if metric.unit == "currency":
        return format_currency(float(value), locale, currency)
    if metric.unit == "percent":
        return format_decimal(float(value), locale, 1) + "%"
    if metric.unit == "count" and float(value).is_integer():
        return format_integer(int(value), locale)
    return format_compact(float(value), locale)


def compose(
    route: Route,
    result: QueryResult,
    locale: str,
    level: str,
    currency: str,
    followups: list[str],
) -> PlainAnswer:
    """Fill the frames from a verified router result."""
    period = (
        route.period.label if route.period and route.period.label else frame(locale, "period.all")
    )
    metric_name = route.metric.label if route.metric else ""
    if locale == "uz" and route.metric is not None:
        metric_name = str((route.metric.i18n.get("uz") or {}).get("label") or route.metric.label)
    n = result.row_count
    if n == 1 and len(result.columns) == 1:
        value = format_cell(result.rows[0][0], route.metric, locale, currency)
        headline = frame(locale, "headline.kpi", metric=metric_name, period=period, value=value)
        explanation = frame(locale, "explanation.kpi")
        next_step = frame(locale, "next_step.kpi")
    else:
        headline = frame(locale, "headline.table", metric=metric_name, n=n)
        explanation = frame(locale, "explanation.table", n=n)
        next_step = frame(locale, "next_step.table")
    direction = "neutral"
    if route.metric is not None:
        direction = (
            "up"
            if route.metric.good_direction == "up"
            else ("down" if route.metric.good_direction == "down" else "neutral")
        )
    details = result.sql if level == "expert" else ""
    return PlainAnswer(
        status="ok",
        headline=headline,
        explanation=explanation,
        so_what=frame(locale, f"so_what.{direction}"),
        next_step=next_step,
        details=details,
        badge=frame(locale, "badge.verified"),
        verified=True,
        used_filters=[route.value[1]] if route.value else [],
        period=period,
        chart=choose_chart(result),
        table=result,
        sql=result.sql if level == "expert" else None,
        followups=followups,
    )


def clarify_answer(route: Route, locale: str) -> PlainAnswer:
    """A short clarifying question with buttons."""
    options = [ClarificationOption(label=o, append=o) for o in route.options]
    return PlainAnswer(
        status="clarify",
        headline=route.clarify or "",
        explanation="",
        badge="",
        options=options,
    )


def refuse_answer(text: str) -> PlainAnswer:
    """A polite refusal."""
    return PlainAnswer(status="refuse", headline=text, explanation="")


def failed_answer(text: str, locale: str) -> PlainAnswer:
    """The question could not be answered (offline or a failed LLM path)."""
    return PlainAnswer(status="failed", headline=text or frame(locale, "offline"), explanation="")
