"""Run detectors and return frame-based insight cards."""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.errors import AppError
from app.models import Dataset, Workspace
from app.schemas.insights import InsightCardOut, InsightsOut, InsightStatusIn, NextStepsOut
from app.services.datasets import Services, stored_model
from app.services.engine import core_settings, domain_pack
from core.insights.pipeline import build_plain_insights
from core.llm.gateway import LLMGateway


def run_insights(
    services: Services, row: Dataset, locale: str, level: str, db: Session
) -> InsightsOut:
    """Compute cards for the current unfiltered dataset."""
    stored = stored_model(row)
    if stored.built is None:
        raise AppError("not_found")
    workspace = db.get(Workspace, row.workspace_id)
    currency = workspace.currency if workspace else "UZS"
    pack = domain_pack(row, stored) or "generic"
    settings = core_settings(services.settings)
    gateway = LLMGateway(settings, services.audit)
    with services.handles.use(row.id, row.storage_name, stored) as handle:
        bundle = build_plain_insights(
            handle.workspace.con,
            handle.model,
            settings,
            pack,
            locale,
            level,
            currency,
            gateway,
        )
    cards = []
    for card in bundle.cards:
        cards.append(
            InsightCardOut(
                finding_id=card.finding_id,
                type=card.type,
                direction=card.direction,
                level=card.level,
                headline=card.headline,
                meaning=card.meaning,
                next_step=card.next_step,
                details=card.details,
                evidence=list(card.evidence),
                priority=card.priority,
                confidence=card.confidence,
                assessment=card.assessment,
                polished=card.polished,
                status=stored.insight_status.get(card.finding_id, "open"),
            )
        )
    return InsightsOut(
        cards=cards,
        next_steps=NextStepsOut(
            missing=list(bundle.next_steps.missing),
            track=list(bundle.next_steps.track),
            quality=list(bundle.next_steps.quality),
        ),
        notes=list(bundle.report.notes),
    )


def set_status(db: Session, row: Dataset, finding_id: str, body: InsightStatusIn) -> InsightCardOut:
    """Remember open / done / dismissed for one finding id."""
    stored = stored_model(row)
    stored.insight_status[finding_id] = body.status
    row.model_json = stored.model_dump_json()
    db.add(row)
    db.commit()
    return InsightCardOut(
        finding_id=finding_id,
        type="",
        direction="",
        level="",
        headline="",
        meaning="",
        next_step="",
        details="",
        evidence=[],
        priority="low",
        confidence="low",
        assessment="",
        polished=False,
        status=body.status,
    )
