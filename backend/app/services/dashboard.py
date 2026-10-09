"""Resolved dashboard templates and tile queries."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

from sqlalchemy.orm import Session

from app.errors import AppError
from app.models import Dataset
from app.schemas.dashboard import (
    DashboardOut,
    FilterIn,
    LayoutIn,
    LayoutOut,
    SlicerOut,
    TileSpecOut,
)
from app.schemas.datasets import StoredModel
from app.services.datasets import Services, stored_model
from app.services.engine import core_settings, domain_pack
from app.services.kpis import default_period, metrics_of, tile_ids
from core.dashboard.filters import choose_slicers
from core.dashboard.tiles import run_tile
from core.metrics.registry import load_pack
from core.models import FilterSpec
from core.runtime.profile import load_limits
from core.runtime.tile_cache import cache_key, get as cache_get, put as cache_put
from core.templates.resolver import resolve_template


def to_spec(filters: FilterIn) -> FilterSpec:
    """API filters → engine FilterSpec."""
    categorical = {k: list(v) for k, v in filters.categorical.items() if v}
    if filters.extra_column and filters.extra_value:
        categorical.setdefault(filters.extra_column, [])
        if filters.extra_value not in categorical[filters.extra_column]:
            categorical[filters.extra_column].append(filters.extra_value)
    date_range = None
    if filters.date_start and filters.date_end:
        date_range = (filters.date_start, filters.date_end)
    return FilterSpec(date_range=date_range, categorical=categorical, numeric=dict(filters.numeric))


def resolved_tiles(row: Dataset, stored: StoredModel, model, con) -> list[dict]:
    """Template tiles that this dataset can actually show."""
    pack_name = domain_pack(row, stored) or "generic"
    pack = load_pack(pack_name)
    metrics = metrics_of(stored)
    ids = [m.id for m in metrics]
    tiles = tile_ids(stored)
    primary = pack.primary_metric if pack.primary_metric in ids else (ids[0] if ids else None)
    return resolve_template(pack.dashboard_template, model, ids, primary, tiles, con)


def dashboard(services: Services, row: Dataset, locale: str) -> DashboardOut:
    """The grid plus slicers; tile numbers are fetched separately."""
    stored = stored_model(row)
    if stored.built is None:
        raise AppError("not_found")
    with services.handles.use(row.id, row.storage_name, stored) as handle:
        tiles = resolved_tiles(row, stored, handle.model, handle.workspace.con)
        period = default_period(handle.workspace.con, handle.model)
        slicer_names = choose_slicers(handle.model)
        columns = {c.name: c for c in handle.model.columns}
    if stored.layout:
        by_id = {item.get("id"): item for item in stored.layout}
        for tile in tiles:
            override = by_id.get(tile["id"])
            if override and "position" in override:
                tile["position"] = override["position"]
    specs = [
        TileSpecOut(
            id=tile["id"],
            type=tile["type"],
            title=(tile.get("title") or {}).get(locale)
            or (tile.get("title") or {}).get("en")
            or tile["id"],
            w=tile["size"]["w"],
            h=tile["size"]["h"],
            x=tile["position"]["x"],
            y=tile["position"]["y"],
            cross_filter=bool(tile.get("cross_filter")),
            dimension=tile.get("dimension") if isinstance(tile.get("dimension"), str) else None,
            metric_ids=list(tile.get("metric_ids") or []),
        )
        for tile in tiles
    ]
    slicers = []
    for name in slicer_names:
        col = columns.get(name)
        if col is None:
            continue
        slicers.append(
            SlicerOut(column=name, label=name, dtype=col.dtype, values=list(col.categories or []))
        )
    period_out = {"start": period[0].isoformat(), "end": period[1].isoformat()} if period else None
    pack_name = domain_pack(row, stored) or "generic"
    return DashboardOut(
        template=load_pack(pack_name).dashboard_template,
        tiles=specs,
        slicers=slicers,
        period=period_out,
    )


def query_tile(
    services: Services,
    row: Dataset,
    tile_id: str,
    filters: FilterIn,
    locale: str,
    currency: str,
    show_sql: bool,
) -> dict:
    """Numbers for one tile under the given filters."""
    stored = stored_model(row)
    if stored.built is None:
        raise AppError("not_found")
    settings = core_settings(services.settings)
    spec = to_spec(filters)
    key = cache_key(row.id, stored.built.version, tile_id, filters.model_dump())
    cached = cache_get(key)
    if cached is not None:
        payload = dict(cached)
        if not show_sql:
            payload["source_sql"] = None
        return payload
    with services.handles.use(row.id, row.storage_name, stored) as handle:
        tiles = resolved_tiles(row, stored, handle.model, handle.workspace.con)
        tile = next((t for t in tiles if t["id"] == tile_id), None)
        if tile is None:
            raise AppError("not_found")
        metrics = {m.id: m for m in metrics_of(stored)}
        data = run_tile(
            handle.workspace.con, handle.model, tile, metrics, spec, settings, locale, currency
        )
    payload = data.as_dict(show_sql=True)
    cache_put(key, payload)
    if not show_sql:
        payload = {**payload, "source_sql": None}
    return payload


def query_tiles(
    services: Services,
    row: Dataset,
    tile_ids: list[str],
    filters: FilterIn,
    locale: str,
    currency: str,
    show_sql: bool,
) -> list[dict]:
    """Several tiles in one go: cache hits first, then at most two workers on the rest."""
    unique: list[str] = list(dict.fromkeys(tile_ids))
    workers = max(1, load_limits().tile_query_parallelism)

    def one(tile_id: str) -> dict:
        return query_tile(services, row, tile_id, filters, locale, currency, show_sql)

    if len(unique) == 1 or workers == 1:
        return [one(tile_id) for tile_id in unique]
    with ThreadPoolExecutor(max_workers=min(workers, len(unique))) as pool:
        return list(pool.map(one, unique))


def save_layout(db: Session, row: Dataset, body: LayoutIn) -> LayoutOut:
    """Remember the user's tile positions."""
    stored = stored_model(row)
    stored.layout = body.tiles
    row.model_json = stored.model_dump_json()
    db.add(row)
    db.commit()
    db.refresh(row)
    return LayoutOut(tiles=body.tiles, source="user")


def read_layout(row: Dataset) -> LayoutOut:
    stored = stored_model(row)
    if stored.layout:
        return LayoutOut(tiles=stored.layout, source="user")
    return LayoutOut(tiles=[], source="template")
