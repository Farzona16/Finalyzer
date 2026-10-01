"""The KPI Autopilot (spec 6.1): classify, collect metrics, validate, select tiles.

``run_autopilot`` is what runs after "Build". It needs no key: with a model it adds suggested
metrics, without one the pack metrics and the offline heuristics are used. A metric that fails
any check is dropped and listed in ``dropped`` (for the log, not for the user).
"""

from __future__ import annotations

import hashlib
import logging
import math
import time
from dataclasses import dataclass, field
from typing import Any

import duckdb
import pandas as pd

from core.autopilot.classifier import Classification, classify
from core.autopilot.dsl import Context, DslError, Spec, canonical, compile_sql, parse_spec, to_dict
from core.autopilot.heuristics import heuristic_candidates
from core.autopilot.scoring import (
    ScoredMetric,
    column_spreads,
    score_metric,
    select_tiles,
    used_columns,
)
from core.autopilot.suggest import finalize_labels, make_tiebreak, suggest_specs
from core.autopilot.validate import load_frame, spec_columns, validate_spec
from core.config import Settings
from core.errors import UserFacingError
from core.llm.gateway import LLMGateway
from core.metrics.builder import metric_sql
from core.metrics.registry import MetricPack, evaluate_pack, load_pack
from core.models import DatasetModel, Metric
from core.query.executor import execute_query

logger = logging.getLogger("finalyzer.autopilot")

AI_PRIORITY = 45
DEFAULT_PRIORITY = 50
SAME_VALUE_TOLERANCE = 1e-9
PACK_IS_RICH = 4  # with this many pack metrics, heuristics only fill the gaps
HEURISTIC_DISCOUNT = 0.6


@dataclass
class AutopilotResult:
    """Everything the interface needs after a build."""

    classification: Classification
    metrics: list[Metric]
    tiles: list[str]
    scores: dict[str, float]
    values: dict[str, float]
    dropped: list[tuple[str, str]] = field(default_factory=list)
    seconds: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "domain": self.classification.to_dict(),
            "metrics": [m.model_dump(mode="json") for m in self.metrics],
            "tiles": self.tiles,
            "scores": self.scores,
            "values": self.values,
            "seconds": round(self.seconds, 3),
        }


@dataclass
class Candidate:
    """A parsed metric waiting for validation."""

    spec: Spec
    source: str
    priority: int


def spec_metric_id(spec: Spec, source: str) -> str:
    """The id of a spec-based metric (AI ids are prefixed so they never hide a pack metric)."""
    base = spec.id or "m_" + hashlib.sha1(canonical(spec).encode()).hexdigest()[:8]
    return f"ai_{base}" if source == "ai" and not base.startswith("ai_") else base


def requires_of(spec: Spec) -> list[str]:
    """``column:`` requirements of every column a spec reads."""
    return [f"column:{name}" for name in sorted(spec_columns(spec))]


def spec_to_metric(spec: Spec, ctx: Context, source: str, priority: int) -> Metric:
    """A ``Metric`` whose expression is the compiled, checked SQL of the spec."""
    labels, machine = dict(spec.labels), False
    if source == "ai":
        labels, machine = finalize_labels(labels)
    english = labels.get("en") or spec.id.replace("_", " ").strip() or spec.agg
    uzbek = labels.get("uz") or english
    return Metric(
        id=spec_metric_id(spec, source),
        label=english,
        expr=compile_sql(spec, ctx),
        requires=requires_of(spec),
        unit=spec.unit,  # type: ignore[arg-type]
        good_direction=spec.good_direction,  # type: ignore[arg-type]
        additive=spec.agg in ("sum", "count") and spec.unit != "percent",
        description=english,
        priority=priority,
        i18n={"uz": {"label": uzbek, "short": uzbek, "description_plain": uzbek}},
        source=source,  # type: ignore[arg-type]
        ai_translated=machine,
        spec=to_dict(spec),
    )


def parse_candidates(
    raws: list[dict[str, Any]],
    ctx: Context,
    source: str,
    seen: set[str],
    dropped: list[tuple[str, str]],
) -> list[Candidate]:
    """Check raw objects; invalid and repeated ones are dropped."""
    found: list[Candidate] = []
    for raw in raws:
        label = str(raw.get("id", "?")) if isinstance(raw, dict) else "?"
        try:
            spec = parse_spec(raw, ctx)
        except DslError as exc:
            dropped.append((label, f"invalid: {exc}"))
            continue
        key = canonical(spec)
        if key in seen:
            dropped.append((label, "duplicate"))
            continue
        seen.add(key)
        priority = raw.get("priority", DEFAULT_PRIORITY) if source != "ai" else AI_PRIORITY
        found.append(Candidate(spec, source, int(priority)))
    return found


def repeats_pack_total(raw: dict[str, Any], model: DatasetModel) -> bool:
    """A plain sum or average of a money column: the pack already shows it, with its rules."""
    money = {model.roles.get(r) for r in ("amount", "cost", "quantity", "target")}
    return raw.get("agg") in ("sum", "avg") and bool(set(raw.get("columns", [])) & money)


def check_pack_metrics(
    pack: MetricPack,
    model: DatasetModel,
    con: duckdb.DuckDBPyConnection,
    settings: Settings,
    dropped: list[tuple[str, str]],
) -> list[tuple[Metric, float]]:
    """Pack metrics that are available and return one finite number."""
    accepted: list[tuple[Metric, float]] = []
    for status in evaluate_pack(model, pack.pack):
        if not status.available:
            continue
        metric = status.metric
        try:
            sql = f"SELECT {metric_sql(metric, model.roles)} AS value FROM data"
            value = execute_query(con, sql, settings).rows[0][0]
        except (UserFacingError, duckdb.Error) as exc:
            dropped.append((metric.id, f"pack metric failed: {type(exc).__name__}"))
            continue
        if value is None or not math.isfinite(float(value)):
            dropped.append((metric.id, "pack metric has no finite value"))
            continue
        accepted.append((metric, float(value)))
    return accepted


def validate_candidates(
    candidates: list[Candidate],
    ctx: Context,
    con: duckdb.DuckDBPyConnection,
    settings: Settings,
    dropped: list[tuple[str, str]],
) -> list[tuple[Metric, float, Candidate]]:
    """Run the full validation on every candidate; return the survivors with their values."""
    columns: set[str] = set()
    for item in candidates:
        columns |= spec_columns(item.spec)
    frame = load_frame(con, columns) if candidates else pd.DataFrame()
    survivors = []
    for item in candidates:
        result = validate_spec(item.spec, ctx, con, frame, settings)
        if not result.ok or result.value is None:
            dropped.append((item.spec.id or "?", result.reason))
            continue
        survivors.append(
            (spec_to_metric(item.spec, ctx, item.source, item.priority), result.value, item)
        )
    return survivors


def is_repeat(value: float, unit: str, taken: list[tuple[float, str]]) -> bool:
    """Same number and unit as an accepted metric: it is the same metric in other words."""
    return any(
        u == unit and math.isclose(value, v, rel_tol=SAME_VALUE_TOLERANCE, abs_tol=1e-12)
        for v, u in taken
    )


def unique_id(metric: Metric, used: set[str]) -> Metric:
    """Give a metric a free id."""
    new_id, n = metric.id, 2
    while new_id in used:
        new_id, n = f"{metric.id}_{n}", n + 1
    return metric if new_id == metric.id else metric.model_copy(update={"id": new_id})


def assemble(
    pack_metrics: list[tuple[Metric, float]],
    survivors: list[tuple[Metric, float, Candidate]],
    dropped: list[tuple[str, str]],
) -> list[tuple[Metric, float]]:
    """Merge pack metrics and survivors, removing metrics that repeat an earlier one."""
    accepted: list[tuple[Metric, float]] = []
    taken: list[tuple[float, str]] = []
    used: set[str] = set()
    for metric, value in [*pack_metrics, *[(m, v) for m, v, _ in survivors]]:
        if is_repeat(value, metric.unit, taken):
            dropped.append((metric.id, "same value as another metric"))
            continue
        metric = unique_id(metric, used)
        used.add(metric.id)
        taken.append((value, metric.unit))
        accepted.append((metric, value))
    return accepted


def run_autopilot(
    model: DatasetModel,
    con: duckdb.DuckDBPyConnection,
    settings: Settings,
    gateway: LLMGateway | None = None,
    pack_override: str | None = None,
) -> AutopilotResult:
    """Classify the data, collect and validate metrics, and choose the KPI tiles."""
    started = time.perf_counter()
    ctx = Context.from_model(model)
    if pack_override:
        load_pack(pack_override)
        classification = Classification(pack_override, 1.0, {}, [], [], "user")
    else:
        classification = classify(model, make_tiebreak(gateway, model))
    pack = load_pack(classification.pack)
    dropped: list[tuple[str, str]] = []
    pack_metrics = check_pack_metrics(pack, model, con, settings, dropped)
    seen: set[str] = set()
    raws = heuristic_candidates(ctx, statistics=classification.pack == "statistics")
    if len(pack_metrics) >= PACK_IS_RICH:
        raws = [r for r in raws if not repeats_pack_total(r, model)]
    candidates = parse_candidates(raws, ctx, "heuristic", seen, dropped)
    if len(pack_metrics) >= PACK_IS_RICH:
        for item in candidates:
            item.priority = int(item.priority * HEURISTIC_DISCOUNT)
    known = [m.id for m, _ in pack_metrics]
    ai_raws = suggest_specs(gateway, model, classification.pack, known)
    candidates += parse_candidates(ai_raws, ctx, "ai", seen, dropped)
    survivors = validate_candidates(candidates, ctx, con, settings, dropped)
    accepted = assemble(pack_metrics, survivors, dropped)
    frame = load_frame(con, {c for m, _ in accepted for c in used_columns(m, model)})
    spreads = column_spreads(frame if accepted else None)
    scored = [score_metric(m, v, model, spreads) for m, v in accepted]
    scored.sort(key=lambda s: (-s.score, s.metric.id))
    tiles = select_tiles(scored, pack.primary_metric)
    for item in dropped:
        logger.info("autopilot_dropped metric=%s reason=%s", item[0], item[1])
    return build_result(classification, scored, tiles, dropped, time.perf_counter() - started)


def build_result(
    classification: Classification,
    scored: list[ScoredMetric],
    tiles: list[str],
    dropped: list[tuple[str, str]],
    seconds: float,
) -> AutopilotResult:
    """Pack the outcome."""
    return AutopilotResult(
        classification=classification,
        metrics=[s.metric for s in scored],
        tiles=tiles,
        scores={s.metric.id: s.score for s in scored},
        values={s.metric.id: s.value for s in scored},
        dropped=dropped,
        seconds=seconds,
    )
