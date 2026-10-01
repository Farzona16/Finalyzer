"""Dashboard templates (spec 6.3, Appendix A).

A template lists tiles for a 12-column grid. ``resolve_template`` keeps only the tiles whose
requirements are met by the dataset and its metrics, fills in the real column of every
dimension, and lays the tiles out so that no gaps remain (never an empty box).

Requirement forms in ``requires``:

* ``amount``: a role that is mapped,
* ``column:name``: a column of the data,
* ``metric:id``: a metric that the autopilot accepted,
* ``date_column``: any date column,
* ``numeric:N``: at least N numeric columns that are not identifiers,
* ``dimension``: the tile's dimension resolves (also checked implicitly).

A tile with ``unless: [role]`` is dropped when that role *is* mapped (used for alternatives).
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from functools import cache
from pathlib import Path
from typing import Any

import duckdb

from core.autopilot.dsl import Context, is_id_like
from core.ingest.profiler import quote_identifier
from core.models import DatasetModel

TEMPLATE_DIR = Path(__file__).parent / "dashboards"
GRID_COLUMNS = 12
MIN_AUTO_CATEGORIES = 3
MAX_AUTO_CATEGORIES = 12
SPECIAL_DIMENSIONS = (
    "weekday_by_week",
    "correlation",
    "top_pair",
    "describe",
    "stalled_rows",
    "numeric:auto",
)


@dataclass
class TemplateContext:
    """What the resolver knows about one dataset."""

    model: DatasetModel
    metric_ids: list[str]
    primary: str | None
    tiles: list[str]
    auto: str | None = None  # the "auto" dimension column, if one exists
    numeric: list[str] = field(default_factory=list)


def list_templates() -> list[str]:
    """Names of all shipped templates."""
    return sorted(p.stem for p in TEMPLATE_DIR.glob("*.json"))


@cache
def load_template(name: str) -> dict[str, Any]:
    """One template as a dict (raises ``FileNotFoundError`` for an unknown name)."""
    path = TEMPLATE_DIR / f"{name}.json"
    return json.loads(path.read_text(encoding="utf-8"))


def numeric_columns(model: DatasetModel) -> list[str]:
    """Numeric, non-personal, non-identifier columns."""
    ctx = Context.from_model(model)
    return [
        c.name
        for c in model.columns
        if c.dtype in ("integer", "float") and not c.is_pii and not is_id_like(c, ctx)
    ]


def normalized_entropy(counts: list[int]) -> float:
    """Entropy of the counts divided by its maximum (1.0 means perfectly even)."""
    total = sum(counts)
    if total == 0 or len(counts) < 2:
        return 0.0
    entropy = -sum(c / total * math.log(c / total) for c in counts if c)
    return entropy / math.log(len(counts))


def auto_dimension(
    model: DatasetModel, con: duckdb.DuckDBPyConnection | None, prefer: list[str] | None = None
) -> str | None:
    """The most informative low-cardinality column: highest normalized entropy, 3 to 12 values.

    Ties go to ``prefer`` (in order), then to column order. Personal data and identifiers are
    never chosen.
    """
    ctx = Context.from_model(model)
    best: tuple[float, int, str] | None = None
    for column in model.columns:
        ok = column.dtype in ("text", "boolean") and not column.is_pii
        if not ok or not MIN_AUTO_CATEGORIES <= column.distinct <= MAX_AUTO_CATEGORIES:
            continue
        if is_id_like(column, ctx):
            continue
        entropy = category_entropy(con, column.name) if con is not None else 0.5
        rank = prefer.index(column.name) if prefer and column.name in prefer else len(prefer or [])
        key = (round(entropy, 6), -rank, column.name)
        if best is None or key[:2] > best[:2]:
            best = (key[0], key[1], column.name)
    return best[2] if best else None


def category_entropy(con: duckdb.DuckDBPyConnection, column: str) -> float:
    """Normalized entropy of the value counts of one column."""
    name = quote_identifier(column)
    rows = con.execute(
        f"SELECT COUNT(*) FROM data WHERE {name} IS NOT NULL GROUP BY {name}"
    ).fetchall()
    return normalized_entropy([int(r[0]) for r in rows])


def requirement_met(requirement: str, tc: TemplateContext, dimension_ok: bool) -> bool:
    """Whether one entry of ``requires`` holds."""
    model = tc.model
    if requirement == "dimension":
        return dimension_ok
    if requirement == "date_column":
        return any(c.dtype == "date" and not c.is_pii for c in model.columns)
    if requirement.startswith("column:"):
        return requirement[len("column:") :] in {c.name for c in model.columns if not c.is_pii}
    if requirement.startswith("metric:"):
        return requirement[len("metric:") :] in tc.metric_ids
    if requirement.startswith("numeric:"):
        return len(tc.numeric) >= int(requirement[len("numeric:") :])
    return requirement in model.roles


def resolve_dimension(tile: dict[str, Any], tc: TemplateContext) -> tuple[bool, str | None]:
    """``(ok, column)`` for the tile's dimension; tiles without a dimension are always ok."""
    dimension = tile.get("dimension")
    if not dimension:
        return True, None
    if dimension == "auto":
        column = tc.auto
        if tile.get("prefer"):
            column = next(
                (p for p in tile["prefer"] if requirement_met(f"column:{p}", tc, True)), column
            )
        return column is not None, column
    if dimension.startswith("column:"):
        name = dimension[len("column:") :]
        return requirement_met(dimension, tc, True), name
    if dimension.startswith("time:") or dimension in SPECIAL_DIMENSIONS:
        return True, None
    column = tc.model.roles.get(dimension)
    return column is not None, column


def resolve_metrics(tile: dict[str, Any], tc: TemplateContext) -> list[str] | None:
    """Metric ids of a tile with ``$primary`` and ``$tiles`` filled in; ``None`` if none exist."""
    wanted = tile.get("metric_ids")
    if not wanted:
        return []
    found: list[str] = []
    for item in wanted:
        if item == "$primary":
            found += [tc.primary] if tc.primary else []
        elif item == "$tiles":
            found += tc.tiles
        elif item in tc.metric_ids:
            found.append(item)
    return list(dict.fromkeys(found)) or None


def resolve_tile(tile: dict[str, Any], tc: TemplateContext) -> dict[str, Any] | None:
    """The tile with real columns and metrics, or ``None`` when it cannot be shown."""
    if any(role in tc.model.roles for role in tile.get("unless", [])):
        return None
    dimension_ok, column = resolve_dimension(tile, tc)
    if not dimension_ok:
        return None
    if not all(requirement_met(r, tc, dimension_ok) for r in tile.get("requires", [])):
        return None
    metrics = resolve_metrics(tile, tc)
    if metrics is None:
        return None
    resolved = {k: v for k, v in tile.items() if k not in ("unless", "prefer")}
    resolved["metric_ids"] = metrics
    resolved["dimension"] = column or tile.get("dimension")
    return resolved


def layout(tiles: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Place tiles left to right on the 12-column grid, starting a new row when one is full."""
    x = y = row_height = 0
    placed = []
    for tile in tiles:
        width, height = tile["size"]["w"], tile["size"]["h"]
        if x + width > GRID_COLUMNS:
            x, y, row_height = 0, y + row_height, 0
        placed.append({**tile, "position": {"x": x, "y": y}})
        x += width
        row_height = max(row_height, height)
    return placed


def resolve_template(
    name: str,
    model: DatasetModel,
    metric_ids: list[str],
    primary: str | None,
    tiles: list[str],
    con: duckdb.DuckDBPyConnection | None = None,
) -> list[dict[str, Any]]:
    """The tiles of a template that can be shown for this dataset, laid out on the grid."""
    template = load_template(name)
    tc = TemplateContext(model, metric_ids, primary, tiles, numeric=numeric_columns(model))
    tc.auto = auto_dimension(model, con)
    kept = []
    for tile in template["tiles"]:
        if tile["type"] == "kpi_row" and not tiles:
            continue
        resolved = resolve_tile(tile, tc)
        if resolved is not None:
            kept.append(resolved)
    return layout(kept)
