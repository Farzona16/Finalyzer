"""Insights (`/api/v1/datasets/{id}/insights`)."""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.orm import Session

from app.deps import dataset_of, get_db, get_services
from app.errors import locale_of
from app.models import Dataset
from app.schemas.insights import InsightCardOut, InsightsOut, InsightStatusIn
from app.security.permissions import Permission
from app.services import insights as service
from app.services.datasets import Services

router = APIRouter(prefix="/datasets", tags=["insights"])
Lang = Literal["uz", "en"]
Level = Literal["simple", "expert", "simpler"]


@router.get("/{dataset_id}/insights", response_model=InsightsOut)
def get_insights(
    request: Request,
    lang: Lang | None = Query(default=None),
    level: Level = Query(default="simple"),
    dataset: Dataset = Depends(dataset_of(Permission.READ)),
    db: Session = Depends(get_db),
    services: Services = Depends(get_services),
) -> InsightsOut:
    """Detector cards in plain language, plus Next steps."""
    return service.run_insights(services, dataset, lang or locale_of(request), level, db)


@router.patch("/{dataset_id}/insights/{finding_id}", response_model=InsightCardOut)
def patch_insight(
    finding_id: str,
    body: InsightStatusIn,
    dataset: Dataset = Depends(dataset_of(Permission.READ)),
    db: Session = Depends(get_db),
) -> InsightCardOut:
    """Mark a card as done or dismissed. Finding ids are per dataset, not global."""
    return service.set_status(db, dataset, finding_id, body)
