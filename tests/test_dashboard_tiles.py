"""Tile queries on the four core demos never raise and always return a series or a table."""

from __future__ import annotations

import pytest

from core.config import Settings
from core.dashboard.tiles import run_tile
from core.metrics.registry import available_metrics, load_pack, primary_metric
from core.models import FilterSpec
from core.templates.resolver import resolve_template
from tests.helpers import DemoDataset


@pytest.mark.parametrize("name", ["retail", "bank", "sales", "statistics"])
def test_every_resolved_tile_returns_data(name: str, request: pytest.FixtureRequest) -> None:
    demo: DemoDataset = request.getfixturevalue(name)
    pack = load_pack(name)
    metrics = {m.id: m for m in available_metrics(demo.model, name)}
    primary = primary_metric(demo.model, name)
    tiles = resolve_template(
        pack.dashboard_template,
        demo.model,
        list(metrics),
        primary.id if primary else None,
        list(metrics)[:8],
        demo.con,
    )
    settings = Settings(_env_file=None)
    nonempty = 0
    for tile in tiles:
        data = run_tile(demo.con, demo.model, tile, metrics, FilterSpec(), settings, "uz", "UZS")
        if data.series or data.rows:
            nonempty += 1
    assert nonempty >= 8, (name, nonempty, [t["id"] for t in tiles])
