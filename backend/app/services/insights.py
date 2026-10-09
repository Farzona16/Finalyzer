"""Run detectors and return frame-based insight cards."""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.errors import AppError
from app.models import Dataset, Workspace
from app.schemas.insights import InsightCardOut, InsightsOut, InsightStatusIn, NextStepsOut
from app.services.datasets import Services, stored_model
from app.services.engine import core_settings, domain_pack, make_gateway
from core.insights.pipeline import build_plain_insights
from core.llm.gateway import LLMGateway
from core.runtime.profile import load_limits
from core.runtime.sampling import sample_badge


def cache_key(gateway: LLMGateway, locale: str, level: str) -> str:
    """Cache identity: provider + model + language + reading level (WP2)."""
    names = gateway.provider_names()
    provider = names[0] if names else "offline"
    return f"{provider}:{gateway.primary_model}:{locale}:{level}"


def _with_status(out: InsightsOut, stored) -> InsightsOut:
    """Apply the user's open/done/dismissed marks onto cached or fresh cards."""
    for card in out.cards:
        card.status = stored.insight_status.get(card.finding_id, card.status)
    return out


def run_insights(
    services: Services, row: Dataset, locale: str, level: str, db: Session
) -> InsightsOut:
    """Compute cards for the current unfiltered dataset, or return the cached bundle."""
    stored = stored_model(row)
    if stored.built is None:
        raise AppError("not_found")
    settings = core_settings(services.settings)
    gateway = make_gateway(services, row, stored, settings)
    key = cache_key(gateway, locale, level)
    if stored.insight_cache_key == key and stored.insight_payload:
        return _with_status(InsightsOut.model_validate(stored.insight_payload), stored)
    workspace = db.get(Workspace, row.workspace_id)
    currency = workspace.currency if workspace else "UZS"
    pack = domain_pack(row, stored) or "generic"
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
    cap = load_limits().analyst_findings
    cards = []
    for card in bundle.cards[:cap]:
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
    out = InsightsOut(
        cards=cards,
        next_steps=NextStepsOut(
            missing=list(bundle.next_steps.missing),
            track=list(bundle.next_steps.track),
            quality=list(bundle.next_steps.quality),
        ),
        notes=[
            note for note in [*bundle.report.notes, sample_badge(stored.sample_fraction)] if note
        ],
    )
    stored.insight_cache_key = key
    stored.insight_payload = out.model_dump()
    row.model_json = stored.model_dump_json()
    db.add(row)
    db.commit()
    return out


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
