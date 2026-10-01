"""Deterministic "Next steps": what data to add, what to track, what to fix."""

from __future__ import annotations

from pydantic import BaseModel

from core.insights.engine import InsightReport
from core.metrics.registry import MetricStatus
from core.models import DatasetModel, Finding, Metric

MAX_TRACKED = 3
ROLE_ADVICE = {
    "cost": "No cost column: gross profit and margin could not be computed. "
    "Add one to unlock them.",
    "quantity": "No quantity column: units sold could not be computed. Add one to unlock it.",
    "customer_id": "No customer ID column: active customers and churn could not be computed. "
    "Add one to unlock them.",
    "date": "No date column: trends, anomalies and period comparisons could not be computed. "
    "Add one to unlock them.",
    "amount": "No amount column: revenue and other money metrics could not be computed. "
    "Add one to unlock them.",
    "status": "No status column: metrics that depend on completed or paid rows count all rows. "
    "Add one to unlock them.",
}
ROLE_ORDER = ("date", "amount", "cost", "quantity", "customer_id", "status")


class NextSteps(BaseModel):
    """The three blocks of the Next steps section."""

    missing: list[str]
    track: list[str]
    quality: list[str]


def missing_advice(model: DatasetModel, unavailable: list[MetricStatus]) -> list[str]:
    """One line per missing role, then one line per metric that is unavailable for other reasons."""
    lines = [ROLE_ADVICE[role] for role in ROLE_ORDER if role not in model.roles]
    for status in unavailable:
        if not status.reason.startswith("Missing role:"):
            lines.append(f"{status.metric.label} is unavailable: {status.reason}.")
    return lines


def largest_trend(report: InsightReport, primary_id: str) -> Finding | None:
    """The strongest flagged trend of a metric other than the primary one."""
    trends = [f for f in report.findings if f.type == "trend" and f.metric_id != primary_id]
    return max(trends, key=lambda f: f.severity, default=None)


def tracking_advice(
    report: InsightReport, metrics: list[Metric], primary: Metric | None
) -> list[str]:
    """Up to three metrics to follow every month, each with a reason."""
    lines: list[str] = []
    if primary is not None:
        lines.append(f"{primary.label}: your main metric, so check it every month.")
    trend = largest_trend(report, report.primary_metric_id)
    if trend is not None:
        label = str(trend.facts.get("metric_label", trend.metric_id))
        lines.append(f"{label}: it showed the largest move among the other metrics.")
    churn = next((f for f in report.findings if f.type == "churn"), None)
    if churn is not None:
        lines.append("Customer churn rate: it changed sharply, so watch whether it settles.")
    else:
        rate = next(
            (m for m in metrics if m.unit == "percent" and m.good_direction == "down"), None
        )
        if rate is not None:
            lines.append(f"{rate.label}: a rate where lower is better; a quick early warning.")
    return lines[:MAX_TRACKED]


def quality_advice(model: DatasetModel, report: InsightReport) -> list[str]:
    """Data-quality fixes from findings, removed personal columns and loading warnings."""
    lines = []
    for finding in report.findings:
        if finding.type != "data_quality":
            continue
        if "column" in finding.facts:
            lines.append(
                f"Column {finding.facts['column']} is {float(finding.facts['null_pct']):.0f}% "
                "empty: fill it in, or leave it out of decisions until it is fixed."
            )
        else:
            lines.append(f"{finding.title}: check whether an export step dropped those days.")
    if model.dropped_pii:
        lines.append(
            "Personal data columns were removed from the analysis: "
            + ", ".join(model.dropped_pii) + "."
        )
    lines += [w for w in model.warnings[:3]]
    return lines


def build_next_steps(
    model: DatasetModel,
    metrics: list[Metric],
    primary: Metric | None,
    unavailable: list[MetricStatus],
    report: InsightReport,
) -> NextSteps:
    """Assemble the Next steps block."""
    return NextSteps(
        missing=missing_advice(model, unavailable),
        track=tracking_advice(report, metrics, primary),
        quality=quality_advice(model, report),
    )
