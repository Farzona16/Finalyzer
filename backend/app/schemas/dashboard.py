"""Dashboard and tile-query request/response models (spec section 10)."""

from __future__ import annotations

from datetime import date
from typing import Any, Literal

from pydantic import BaseModel, Field

from app.schemas.auth import StrictModel


class FilterIn(StrictModel):
    """Slicers plus an optional cross-filter from a chart click."""

    date_start: date | None = None
    date_end: date | None = None
    categorical: dict[str, list[str]] = Field(default_factory=dict)
    numeric: dict[str, tuple[float, float]] = Field(default_factory=dict)
    extra_column: str | None = None
    extra_value: str | None = None


class TileQueryIn(StrictModel):
    """Ask for the data of one resolved tile."""

    tile_id: str = Field(min_length=1, max_length=80)
    filters: FilterIn = Field(default_factory=FilterIn)


class TileBatchIn(StrictModel):
    """Ask for several tiles in one request (deduped, cached, two workers)."""

    tile_ids: list[str] = Field(min_length=1, max_length=40)
    filters: FilterIn = Field(default_factory=FilterIn)


class TileSpecOut(BaseModel):
    """One box on the grid, without its query result (that comes from POST /tiles/query)."""

    id: str
    type: str
    title: str
    w: int
    h: int
    x: int
    y: int
    cross_filter: bool = False
    dimension: str | None = None
    metric_ids: list[str] = Field(default_factory=list)


class SlicerOut(BaseModel):
    """One filter control on the dashboard bar."""

    column: str
    label: str
    dtype: str
    values: list[str] = Field(default_factory=list)


class DashboardOut(BaseModel):
    """GET /datasets/{id}/dashboard."""

    template: str
    tiles: list[TileSpecOut]
    slicers: list[SlicerOut]
    period: dict[str, str] | None = None


class LayoutIn(StrictModel):
    """User-moved tile positions (Should)."""

    tiles: list[dict[str, Any]]


class LayoutOut(BaseModel):
    """Stored layout, or the template layout when the user has not moved tiles."""

    tiles: list[dict[str, Any]]
    source: Literal["user", "template"]
