"""Run all detectors on the current ``data`` view and collect the findings."""

from __future__ import annotations

import logging
from collections.abc import Callable

import duckdb
from pydantic import BaseModel

from core.config import Settings
from core.insights.context import InsightContext, data_bounds, last_complete_month, month_label
from core.insights.quality import detect_date_gaps, detect_null_columns
from core.insights.ranking import rank_findings
from core.insights.segment_detectors import (
    detect_concentration,
    detect_drivers,
    detect_segment_gaps,
    detect_segment_momentum,
)
from core.insights.time_detectors import detect_anomalies, detect_churn, detect_trends
from core.models import DatasetModel, Finding, Metric

logger = logging.getLogger("finalyzer.insights")


class InsightReport(BaseModel):
    """All findings (ranked, best first) plus the reasons some checks did not run."""

    findings: list[Finding]
    notes: list[str]
    primary_metric_id: str
    last_complete_month: str | None = None
    data_end: str | None = None


def guarded(ctx: InsightContext, name: str, run: Callable[[], list[Finding]]) -> list[Finding]:
    """Run one detector; a failure only adds a note and never stops the others."""
    try:
        return run()
    except Exception as exc:  # noqa: BLE001 - reported as a note, details go to the log
        logger.warning("detector_failed name=%s type=%s", name, type(exc).__name__)
        ctx.note(f"The {name} check could not be completed.")
        return []


def run_detectors(
    con: duckdb.DuckDBPyConnection,
    model: DatasetModel,
    metrics: list[Metric],
    primary: Metric | None,
    settings: Settings,
) -> InsightReport:
    """Detect trends, anomalies, concentration, drivers, gaps, momentum, churn, data quality."""
    if primary is None:
        return InsightReport(findings=[], notes=["No metric is available for analysis."],
                             primary_metric_id="")
    ctx = InsightContext(con, model, metrics, primary, settings)
    bounds = guarded_bounds(ctx)
    findings: list[Finding] = []
    if bounds is not None:
        trends = guarded(ctx, "trend", lambda: detect_trends(ctx, bounds))
        findings += trends
        findings += guarded(ctx, "driver", lambda: detect_drivers(ctx, trends))
        findings += guarded(ctx, "anomaly", lambda: detect_anomalies(ctx, bounds))
        findings += guarded(ctx, "momentum", lambda: detect_segment_momentum(ctx, bounds))
        findings += guarded(ctx, "churn", lambda: detect_churn(ctx, bounds))
        findings += guarded(ctx, "date gap", lambda: detect_date_gaps(ctx))
    else:
        ctx.note("Time-based checks skipped: no date column or no rows in view.")
    findings += guarded(ctx, "concentration", lambda: detect_concentration(ctx))
    findings += guarded(ctx, "segment gap", lambda: detect_segment_gaps(ctx))
    findings += guarded(ctx, "data quality", lambda: detect_null_columns(ctx))
    return InsightReport(
        findings=rank_findings(findings),
        notes=ctx.notes,
        primary_metric_id=primary.id,
        last_complete_month=month_label(last_complete_month(bounds)) if bounds else None,
        data_end=bounds[1].isoformat() if bounds else None,
    )


def guarded_bounds(ctx: InsightContext) -> tuple | None:
    """Date span of the data in view; None when it cannot be determined."""
    try:
        return data_bounds(ctx)
    except Exception as exc:  # noqa: BLE001
        logger.warning("bounds_failed type=%s", type(exc).__name__)
        return None
