"""KPI selection (spec 6.1, step 6).

``score = 0.45 * priority + 0.25 * variability + 0.15 * coverage + 0.15 * trust`` (all 0 to 1):

* priority: the pack's weight of the metric (1 to 100),
* variability: how interesting the number is. Shares near 0% or 100% are dull, shares near 50%
  are not; for other metrics the spread of the underlying column (coefficient of variation),
* coverage: how complete the used columns are,
* trust: how much we rely on the source (pack 1.0, heuristics 0.8, AI 0.7). Every metric here
  has already passed validation, so this is the validation score of the spec.

The best ``MAX_TILES`` become KPI tiles, the primary metric first. The rest stay in the KPI
manager.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import pandas as pd

from core.models import DatasetModel, Metric

MAX_TILES = 8
MAX_TILES_PER_COLUMN = 2
WEIGHTS = {"priority": 0.45, "variability": 0.25, "coverage": 0.15, "trust": 0.15}
TRUST = {"pack": 1.0, "heuristic": 0.8, "ai": 0.7, "user": 1.0}
DEFAULT_VARIABILITY = 0.5


@dataclass(frozen=True)
class ScoredMetric:
    """An accepted metric with its value and selection score."""

    metric: Metric
    value: float
    score: float
    columns: tuple[str, ...] = ()
    crowd: tuple[str, ...] = ()  # columns that limit how many tiles may repeat them


def used_columns(metric: Metric, model: DatasetModel) -> tuple[str, ...]:
    """The data columns a metric reads, from its ``requires`` list."""
    names: list[str] = []
    for requirement in metric.requires:
        if requirement.startswith("column:"):
            names.append(requirement[len("column:") :])
        elif requirement in model.roles:
            names.append(model.roles[requirement])
    return tuple(dict.fromkeys(names))


def coverage(columns: tuple[str, ...], model: DatasetModel) -> float:
    """Share of non-empty cells of the least complete used column (1.0 without columns)."""
    nulls = {c.name: c.null_pct for c in model.columns}
    shares = [1.0 - nulls.get(name, 0.0) / 100.0 for name in columns]
    return max(0.0, min(shares)) if shares else 1.0


def column_spreads(frame: pd.DataFrame | None) -> dict[str, float]:
    """Coefficient of variation (capped at 1) of every numeric column of the frame."""
    spreads: dict[str, float] = {}
    for name in () if frame is None else frame.columns:
        values = pd.to_numeric(frame[name], errors="coerce").dropna()
        if len(values) > 1 and abs(values.mean()) > 0:
            spreads[name] = min(1.0, float(values.std(ddof=1) / abs(values.mean())))
    return spreads


def spread(spreads: dict[str, float], columns: tuple[str, ...]) -> float | None:
    """Spread of the first used column that has one."""
    return next((spreads[name] for name in columns if name in spreads), None)


def variability(metric: Metric, value: float, cv: float | None) -> float:
    """0 to 1: how much the number tells a reader."""
    if metric.unit == "percent":
        share = min(1.0, max(0.0, value / 100.0))
        return 4.0 * share * (1.0 - share)
    return DEFAULT_VARIABILITY if cv is None else cv


def score_metric(
    metric: Metric, value: float, model: DatasetModel, spreads: dict[str, float] | None = None
) -> ScoredMetric:
    """The selection score of one accepted metric."""
    columns = used_columns(metric, model)
    parts = {
        "priority": max(0.0, min(1.0, metric.priority / 100.0)),
        "variability": variability(metric, value, spread(spreads or {}, columns)),
        "coverage": coverage(columns, model),
        "trust": TRUST.get(metric.source, 0.7),
    }
    total = sum(WEIGHTS[k] * v for k, v in parts.items())
    if not math.isfinite(total):
        total = 0.0
    crowd = columns if metric.source != "pack" else ()
    return ScoredMetric(metric, value, round(total, 6), columns, crowd)


def crowded(item: ScoredMetric, per_column: dict[str, int]) -> bool:
    """True when a column already feeds the allowed number of tiles."""
    return any(per_column.get(name, 0) >= MAX_TILES_PER_COLUMN for name in item.crowd)


def select_tiles(
    scored: list[ScoredMetric], primary_id: str | None, limit: int = MAX_TILES
) -> list[str]:
    """Ids of the KPI tiles: the primary metric first, then the best by score."""
    ranked = sorted(scored, key=lambda s: (-s.score, s.metric.id))
    chosen: list[ScoredMetric] = [s for s in ranked if s.metric.id == primary_id][:1]
    per_column: dict[str, int] = {}
    for item in chosen:
        for name in item.crowd:
            per_column[name] = per_column.get(name, 0) + 1
    for item in ranked:
        if len(chosen) >= limit:
            break
        if item in chosen or crowded(item, per_column):
            continue
        chosen.append(item)
        for name in item.crowd:
            per_column[name] = per_column.get(name, 0) + 1
    return [s.metric.id for s in chosen]
