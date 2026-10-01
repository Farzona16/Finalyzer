"""One call that produces everything the Insights tab shows."""

from __future__ import annotations

import logging
from dataclasses import dataclass

import duckdb
from pydantic import BaseModel

from core.config import Settings
from core.i18n.frames import FrameError
from core.insights.engine import InsightReport, run_detectors
from core.insights.narrator import NarrationResult, narrate_findings
from core.insights.nextsteps import NextSteps, build_next_steps
from core.insights.nextsteps_v3 import build_next_steps_v3
from core.insights.plain import PlainCard, PlainContext, explain
from core.insights.polish import polish_card
from core.insights.ranking import TOP_COUNT
from core.insights.runner import run_insights
from core.llm.gateway import LLMGateway
from core.metrics.registry import (
    MetricStatus,
    available_metrics,
    primary_metric,
    unavailable_metrics,
)
from core.models import DatasetModel, Metric

logger = logging.getLogger("finalyzer.insights")


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


@dataclass
class PlainInsights:
    """What the Insights page shows: ranked cards in one language and level, and next steps."""

    report: InsightReport
    cards: list[PlainCard]
    next_steps: NextSteps
    top_count: int = TOP_COUNT


def build_plain_insights(
    con: duckdb.DuckDBPyConnection,
    model: DatasetModel,
    settings: Settings,
    pack: str,
    locale: str = "uz",
    level: str = "simple",
    currency: str = "UZS",
    gateway: LLMGateway | None = None,
) -> PlainInsights:
    """Run every detector of the pack and write the cards from message frames.

    A card whose finding cannot be worded is left out and logged; the others are unaffected.
    With ``UZ_POLISH`` on, Uzbek cards may be rephrased by the model (and are checked first).
    """
    metrics = available_metrics(model, pack)
    primary = primary_metric(model, pack)
    report = run_insights(con, model, settings, pack, metrics)
    ctx = PlainContext(locale, currency, {m.id: m for m in metrics})
    cards: list[PlainCard] = []
    for finding in report.findings:
        try:
            card = explain(finding, ctx, level)
        except (FrameError, KeyError, ValueError) as exc:
            logger.warning("card_skipped type=%s reason=%s", finding.type, type(exc).__name__)
            continue
        cards.append(polish_card(card, finding, gateway, settings))
    steps = build_next_steps_v3(
        model, metrics, primary, unavailable_metrics(model, pack), report, ctx
    )
    return PlainInsights(report=report, cards=cards, next_steps=steps)
