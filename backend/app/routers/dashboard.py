"""Dashboard (`/api/v1/datasets/{id}/dashboard`) and tile queries."""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.orm import Session

from app.deps import dataset_of, get_db, get_services, require
from app.errors import locale_of
from app.models import Dataset, User, Workspace
from app.schemas.dashboard import DashboardOut, LayoutIn, LayoutOut, TileBatchIn, TileQueryIn
from app.security.permissions import Permission, has_permission
from app.services import dashboard as service
from app.services.datasets import Services

router = APIRouter(prefix="/datasets", tags=["dashboard"])
Lang = Literal["uz", "en"]


@router.get("/{dataset_id}/dashboard", response_model=DashboardOut)
def get_dashboard(
    request: Request,
    lang: Lang | None = Query(default=None),
    dataset: Dataset = Depends(dataset_of(Permission.READ)),
    services: Services = Depends(get_services),
) -> DashboardOut:
    """Resolved template: tile boxes, slicers and the default period. Numbers come next."""
    return service.dashboard(services, dataset, lang or locale_of(request))


@router.post("/{dataset_id}/tiles/query")
def query_tile(
    body: TileQueryIn,
    request: Request,
    lang: Lang | None = Query(default=None),
    dataset: Dataset = Depends(dataset_of(Permission.READ)),
    user: User = Depends(require(Permission.READ)),
    db: Session = Depends(get_db),
    services: Services = Depends(get_services),
) -> dict[str, Any]:
    """Data for one tile. ``source_sql`` is present only for analysts and admins."""
    workspace = db.get(Workspace, user.workspace_id)
    currency = workspace.currency if workspace else "UZS"
    return service.query_tile(
        services,
        dataset,
        body.tile_id,
        body.filters,
        lang or locale_of(request),
        currency,
        show_sql=has_permission(user.role, Permission.SEE_SQL),
    )


@router.post("/{dataset_id}/tiles/query-batch")
def query_tiles_batch(
    body: TileBatchIn,
    request: Request,
    lang: Lang | None = Query(default=None),
    dataset: Dataset = Depends(dataset_of(Permission.READ)),
    user: User = Depends(require(Permission.READ)),
    db: Session = Depends(get_db),
    services: Services = Depends(get_services),
) -> list[dict[str, Any]]:
    """Data for several tiles. Identical SQL is served from the LRU cache."""
    workspace = db.get(Workspace, user.workspace_id)
    currency = workspace.currency if workspace else "UZS"
    return service.query_tiles(
        services,
        dataset,
        body.tile_ids,
        body.filters,
        lang or locale_of(request),
        currency,
        show_sql=has_permission(user.role, Permission.SEE_SQL),
    )


@router.get("/{dataset_id}/layout", response_model=LayoutOut)
def get_layout(dataset: Dataset = Depends(dataset_of(Permission.READ))) -> LayoutOut:
    """Stored tile positions, if the user has moved them."""
    return service.read_layout(dataset)


@router.put("/{dataset_id}/layout", response_model=LayoutOut)
def put_layout(
    body: LayoutIn,
    dataset: Dataset = Depends(dataset_of(Permission.EDIT_KPIS)),
    db: Session = Depends(get_db),
) -> LayoutOut:
    """Save a drag-resized layout."""
    return service.save_layout(db, dataset, body)
