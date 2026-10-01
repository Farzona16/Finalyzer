"""Insights v2 orchestration: the v2 detectors plus the detectors a pack asks for.

``run_insights`` is what the API calls. For most packs it is the v2 engine followed by the
pack's extra detectors. The sales pack needs two time axes: outcomes (won revenue, win
counts, deal size) belong to the day a deal *closed*, while the win *rate* of deals is judged
by when they were *created*; mixing them makes the newest month look like a collapse because
its deals are still open.
"""

from __future__ import annotations

import logging
import math
from collections.abc import Callable

import duckdb
from scipy import stats

from core.config import Settings
from core.insights import sales_detectors as sales
from core.insights import stats_detectors as stats_mod
from core.insights.context import InsightContext, month_end
from core.insights.engine import InsightReport, guarded, guarded_bounds, run_detectors
from core.insights.ranking import rank_findings
from core.insights.time_detectors import detect_trends
from core.metrics.registry import available_metrics, load_pack, primary_metric
from core.models import DatasetModel, Finding, Metric

logger = logging.getLogger("finalyzer.insights")

OUTCOME_METRICS = ("won_revenue", "deals_won", "deals_lost", "avg_deal_size", "win_rate_pct")
RATE_METRIC = "win_rate_pct"
CYCLE_METRIC = "avg_cycle_days"  # reported by cycle_change, never as a month-to-month trend
CREATED_AXIS_METRIC = (
    "new_deals"  # open pipeline is a stock: the newest months always hold most open deals
)
TREND_P_LIMIT = 0.01

SALES_DETECTORS: dict[str, Callable[[InsightContext], list[Finding]]] = {
    "win_rate_gap": sales.detect_win_rate_gap,
    "funnel_drop": sales.detect_funnel_drop,
    "stalled": sales.detect_stalled,
    "cycle_change": sales.detect_cycle_change,
}


def find_metric(metrics: list[Metric], metric_id: str) -> Metric | None:
    """The metric with this id, if it is available."""
    return next((m for m in metrics if m.id == metric_id), None)


def merge(reports: list[InsightReport], extra: list[Finding]) -> InsightReport:
    """One report from several: duplicates (same id) removed, ranked, notes joined."""
    seen: set[str] = set()
    findings: list[Finding] = []
    for finding in [f for r in reports for f in r.findings] + extra:
        if finding.id not in seen:
            seen.add(finding.id)
            findings.append(finding)
    notes = list(dict.fromkeys(n for r in reports for n in r.notes))
    first = reports[0]
    return InsightReport(
        findings=rank_findings(findings),
        notes=notes,
        primary_metric_id=first.primary_metric_id,
        last_complete_month=first.last_complete_month,
        data_end=first.data_end,
    )


def with_close_axis(model: DatasetModel) -> DatasetModel:
    """The same dataset with the close date as its date column."""
    return model.model_copy(update={"roles": {**model.roles, "date": sales.CLOSE_COLUMN}})


def sales_base(
    con: duckdb.DuckDBPyConnection, model: DatasetModel, metrics: list[Metric], settings: Settings
) -> InsightReport:
    """Outcomes are judged by close date, new deals by creation date."""
    outcomes = [m for m in metrics if m.id in OUTCOME_METRICS]
    won_revenue = find_metric(outcomes, "won_revenue") or (outcomes[0] if outcomes else None)
    if sales.CLOSE_COLUMN not in {c.name for c in model.columns} or won_revenue is None:
        return run_detectors(con, model, metrics, metrics[0] if metrics else None, settings)
    closed_model = with_close_axis(model)
    report = run_detectors(con, closed_model, outcomes, won_revenue, settings)
    report.findings = [f for f in report.findings if not is_win_rate_gap_by_segment(f)]
    ctx = InsightContext(con, closed_model, outcomes, won_revenue, settings)
    report.findings = [
        f for f in report.findings if f.metric_id != RATE_METRIC or win_rate_change_is_real(ctx, f)
    ]
    return merge([report], created_axis_trends(con, model, metrics, settings))


def is_win_rate_gap_by_segment(finding: Finding) -> bool:
    """The win_rate_gap detector covers segment gaps of the win rate; do not say it twice."""
    return finding.type == "segment_gap" and finding.metric_id == RATE_METRIC


def created_axis_trends(
    con: duckdb.DuckDBPyConnection, model: DatasetModel, metrics: list[Metric], settings: Settings
) -> list[Finding]:
    """Trend of the number of new deals, by the month they were created."""
    created = find_metric(metrics, CREATED_AXIS_METRIC)
    if created is None:
        return []
    ctx = InsightContext(con, model, [created], created, settings)
    bounds = guarded_bounds(ctx)
    return guarded(ctx, "trend", lambda: detect_trends(ctx, bounds)) if bounds else []


def win_rate_change_is_real(ctx: InsightContext, finding: Finding) -> bool:
    """Two-proportion test on the closed deals of the two months (p < 0.01).

    A win rate rests on a few hundred closed deals per month, so a swing of a few points can be
    pure chance; the plain 2-point rule of the trend detector cannot tell. Other findings pass.
    """
    if finding.type != "trend":
        return True
    counts = []
    for key in ("last_period", "previous_period"):
        start = sales.month_start_of(str(finding.facts[key]))
        counts.append(sales.closed_counts(ctx, start, month_end(start)))
    (won_a, n_a), (won_b, n_b) = counts
    if min(n_a, n_b) < sales.MIN_CLOSED_PER_GROUP:
        return False
    pooled = (won_a + won_b) / (n_a + n_b)
    spread = math.sqrt(pooled * (1 - pooled) * (1 / n_a + 1 / n_b))
    if spread == 0:
        return False
    z = abs(won_a / n_a - won_b / n_b) / spread
    return float(2 * stats.norm.sf(z)) < TREND_P_LIMIT


def run_pack_detectors(
    con: duckdb.DuckDBPyConnection,
    model: DatasetModel,
    primary: Metric,
    metrics: list[Metric],
    settings: Settings,
    wanted: list[str],
) -> tuple[list[Finding], list[str]]:
    """The extra detectors a pack lists: sales ones on SQL, statistics ones on a pandas frame."""
    ctx = InsightContext(con, model, metrics, primary, settings)
    findings: list[Finding] = []
    for name in wanted:
        if name in SALES_DETECTORS:
            findings += guarded(ctx, name, lambda n=name: SALES_DETECTORS[n](ctx))
    stat_names = [n for n in wanted if n in stats_mod.DETECTORS]
    data = guarded_data(ctx) if stat_names else None
    if data is not None:
        for name in stat_names:
            findings += guarded(ctx, name, lambda n=name: stats_mod.DETECTORS[n](ctx, data))
    return findings, ctx.notes


def guarded_data(ctx: InsightContext) -> stats_mod.StatsData | None:
    """Load the statistics frame; a failure is a note and not an error."""
    try:
        return stats_mod.load_data(ctx)
    except Exception as exc:  # noqa: BLE001
        logger.warning("stats_load_failed type=%s", type(exc).__name__)
        ctx.note("The statistical checks could not be completed.")
        return None


def run_insights(
    con: duckdb.DuckDBPyConnection,
    model: DatasetModel,
    settings: Settings,
    pack_name: str,
    metrics: list[Metric] | None = None,
) -> InsightReport:
    """Every detector that applies to this dataset's pack, ranked."""
    metrics = metrics if metrics is not None else available_metrics(model, pack_name)
    primary = primary_metric(model, pack_name)
    if primary is None:
        return run_detectors(con, model, metrics, None, settings)
    if pack_name == "sales":
        report = sales_base(con, model, metrics, settings)
    else:
        report = run_detectors(con, model, metrics, primary, settings)
    wanted = load_pack(pack_name).detectors
    extra, notes = run_pack_detectors(con, model, primary, metrics, settings, wanted)
    merged = merge([report], extra)
    merged.notes = list(dict.fromkeys([*merged.notes, *notes]))
    return merged
