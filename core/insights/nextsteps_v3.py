""" "Next steps" in Uzbek and English: what data to add, what to follow, what to fix.

The same three blocks as ``nextsteps.py``; the sentences come from ``nextsteps_text.json``
(listed in ``docs/uz_review.md`` until a native speaker has checked them).
"""

from __future__ import annotations

import json
from functools import cache
from pathlib import Path

from core.insights.engine import InsightReport
from core.insights.nextsteps import MAX_TRACKED, ROLE_ORDER, NextSteps, largest_trend
from core.insights.plain import Fmt, PlainContext
from core.metrics.registry import MetricStatus
from core.models import DatasetModel, Metric

TEXT_FILE = Path(__file__).parent / "nextsteps_text.json"
GAP_TYPE = "date_gap"


@cache
def texts(locale: str) -> dict[str, str]:
    """The sentences of one language."""
    return json.loads(TEXT_FILE.read_text(encoding="utf-8"))[locale]


def say(locale: str, key: str, **values: object) -> str:
    """A sentence with its values filled in."""
    return texts(locale)[key].format(**values)


def missing_lines(model: DatasetModel, unavailable: list[MetricStatus], fmt: Fmt) -> list[str]:
    """One line per missing role, then one per metric unavailable for another reason."""
    lines = [say(fmt.locale, f"missing.{r}") for r in ROLE_ORDER if r not in model.roles]
    for status in unavailable:
        if not status.reason.startswith("Missing role:"):
            name = fmt.metric_text(status.metric.id, status.metric.label)
            lines.append(say(fmt.locale, "missing.metric", metric=name, reason=status.reason))
    return lines


def tracking_lines(
    report: InsightReport, metrics: list[Metric], primary: Metric | None, fmt: Fmt
) -> list[str]:
    """Up to three figures to follow every month, each with a reason."""
    locale, lines = fmt.locale, []
    if primary is not None:
        name = fmt.metric_text(primary.id, primary.label)
        lines.append(say(locale, "track.primary", metric=name))
    trend = largest_trend(report, report.primary_metric_id)
    if trend is not None:
        label = str(trend.facts.get("metric_label", trend.metric_id))
        lines.append(say(locale, "track.trend", metric=fmt.metric_text(trend.metric_id, label)))
    if any(f.type == "churn" for f in report.findings):
        lines.append(say(locale, "track.churn"))
    else:
        rate = next(
            (m for m in metrics if m.unit == "percent" and m.good_direction == "down"), None
        )
        if rate is not None:
            lines.append(say(locale, "track.rate", metric=fmt.metric_text(rate.id, rate.label)))
    return lines[:MAX_TRACKED]


def quality_lines(model: DatasetModel, report: InsightReport, fmt: Fmt) -> list[str]:
    """Fixes for empty columns, date gaps, removed personal columns and loading warnings."""
    lines = []
    for finding in report.findings:
        if finding.type != "data_quality":
            continue
        if "column" in finding.facts:
            pct = fmt.num(finding.facts["null_pct"], 0)
            lines.append(say(fmt.locale, "quality.column", column=finding.facts["column"], pct=pct))
        else:
            lines.append(say(fmt.locale, "quality.gaps"))
    if model.dropped_pii:
        lines.append(say(fmt.locale, "quality.pii", columns=", ".join(model.dropped_pii)))
    return lines + list(model.warnings[:3])


def build_next_steps_v3(
    model: DatasetModel,
    metrics: list[Metric],
    primary: Metric | None,
    unavailable: list[MetricStatus],
    report: InsightReport,
    ctx: PlainContext,
) -> NextSteps:
    """The Next steps block in the language of ``ctx``."""
    fmt = Fmt(ctx)
    return NextSteps(
        missing=missing_lines(model, unavailable, fmt),
        track=tracking_lines(report, metrics, primary, fmt),
        quality=quality_lines(model, report, fmt),
    )
