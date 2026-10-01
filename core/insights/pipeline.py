"""One call that produces everything the Insights tab shows."""

from __future__ import annotations

import duckdb
from pydantic import BaseModel

from core.config import Settings
from core.insights.engine import InsightReport, run_detectors
from core.insights.narrator import NarrationResult, narrate_findings
from core.insights.nextsteps import NextSteps, build_next_steps
from core.insights.ranking import TOP_COUNT
from core.llm.gateway import LLMGateway
from core.metrics.registry import MetricStatus
from core.models import DatasetModel, Metric


class InsightBundle(BaseModel):
    """Findings, cards (top ones first) and next steps for one filter state."""

    report: InsightReport
    narration: NarrationResult
    next_steps: NextSteps
    top_count: int = TOP_COUNT


def build_insights(
    con: duckdb.DuckDBPyConnection,
    model: DatasetModel,
    metrics: list[Metric],
    primary: Metric | None,
    unavailable: list[MetricStatus],
    gateway: LLMGateway | None,
    settings: Settings,
) -> InsightBundle:
    """Run the detectors, write the cards and assemble the next steps."""
    report = run_detectors(con, model, metrics, primary, settings)
    narration = narrate_findings(report.findings, gateway, settings)
    steps = build_next_steps(model, metrics, primary, unavailable, report)
    return InsightBundle(report=report, narration=narration, next_steps=steps)
