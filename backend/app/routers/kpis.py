"""KPIs (`/api/v1/datasets/{id}/kpis`): the autopilot's metrics and the KPI manager."""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.orm import Session

from app.deps import dataset_of, get_db, get_services, require
from app.errors import locale_of
from app.models import Dataset, User, Workspace
from app.schemas.datasets import KpiListOut, KpiPatchIn
from app.security.permissions import Permission, has_permission
from app.services import kpis as service
from app.services.datasets import Services

router = APIRouter(prefix="/datasets", tags=["kpis"])
Lang = Literal["uz", "en"]


def respond(
    request: Request,
    db: Session,
    services: Services,
    dataset: Dataset,
    user: User,
    lang: str | None,
) -> KpiListOut:
    """Build the KPI list in the requested (or the request's) language."""
    workspace = db.get(Workspace, user.workspace_id)
    currency = workspace.currency if workspace else "UZS"
    chosen = service.pick_language(lang or locale_of(request))
    return service.kpi_list(
        services,
        dataset,
        chosen,
        currency,
        show_expert=has_permission(user.role, Permission.SEE_SQL),
    )


@router.get("/{dataset_id}/kpis", response_model=KpiListOut)
def list_kpis(
    request: Request,
    lang: Lang | None = Query(default=None),
    dataset: Dataset = Depends(dataset_of(Permission.READ)),
    user: User = Depends(require(Permission.READ)),
    db: Session = Depends(get_db),
    services: Services = Depends(get_services),
) -> KpiListOut:
    """The domain guess, the dashboard KPIs and every other metric the autopilot accepted."""
    return respond(request, db, services, dataset, user, lang)


@router.patch("/{dataset_id}/kpis/{metric_id}", response_model=KpiListOut)
def patch_kpi(
    metric_id: str,
    body: KpiPatchIn,
    request: Request,
    lang: Lang | None = Query(default=None),
    dataset: Dataset = Depends(dataset_of(Permission.EDIT_KPIS)),
    user: User = Depends(require(Permission.EDIT_KPIS)),
    db: Session = Depends(get_db),
    services: Services = Depends(get_services),
) -> KpiListOut:
    """Show or hide, rename or move one KPI."""
    service.patch_kpi(db, dataset, metric_id, body)
    return respond(request, db, services, dataset, user, lang)
