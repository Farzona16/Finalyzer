"""Metrics Registry: the single source of truth for metric definitions.

Metrics live in YAML packs and are written with role placeholders such as ``{amount}``.
A metric is *available* when all roles it needs are mapped and the category values it needs
(for example ``status`` containing ``completed``) exist in the data. Unavailable metrics are
reported with a reason and never raise errors.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import duckdb
import sqlglot
import yaml
from pydantic import BaseModel
from sqlglot import exp

from core.errors import ValidationError
from core.metrics.builder import metric_sql, placeholders
from core.models import DatasetModel, Metric
from core.query.executor import execute_query

MAX_EXPRESSION_LENGTH = 500
PACK_DIR = Path(__file__).parent / "packs"
COLUMN_PREFIX = "column:"


class MetricPack(BaseModel):
    """A named list of metrics with one primary metric."""

    pack: str
    primary_metric: str
    metrics: list[Metric]


class MetricStatus(BaseModel):
    """Whether one metric can be computed on the current dataset, and why not."""

    metric: Metric
    available: bool
    reason: str = ""


def list_pack_names() -> list[str]:
    """Names of all packs shipped with the app."""
    return sorted(p.stem for p in PACK_DIR.glob("*.yaml"))


@lru_cache(maxsize=8)
def load_pack(name: str) -> MetricPack:
    """Load one pack by name."""
    path = PACK_DIR / f"{name}.yaml"
    if not path.is_file():
        raise ValidationError(f"Unknown metric pack '{name}'.")
    with open(path, encoding="utf-8") as handle:
        return MetricPack.model_validate(yaml.safe_load(handle))


def column_categories(model: DatasetModel, column: str) -> list[str] | None:
    """Category values of a flat column (None if it has more than 30 distinct values)."""
    for info in model.columns:
        if info.name == column:
            return info.categories
    return None


def choose_pack(model: DatasetModel) -> str:
    """Pick ``retail``, ``bank`` or ``generic`` from the mapped roles and status values."""
    roles = model.roles
    if all(role in roles for role in ("order_id", "amount", "quantity")):
        return "retail"
    if "status" in roles and "amount" in roles:
        values = set(column_categories(model, roles["status"]) or [])
        if {"completed", "failed"} <= values:
            return "bank"
    return "generic"


def requirement_problem(requirement: str, model: DatasetModel) -> str | None:
    """Return a message when a role (or literal column) needed by a metric is missing."""
    if requirement.startswith(COLUMN_PREFIX):
        column = requirement[len(COLUMN_PREFIX):]
        if column not in {c.name for c in model.columns}:
            return f"Missing column: {column}"
        return None
    if requirement not in model.roles:
        return f"Missing role: {requirement}"
    return None


def value_problem(metric: Metric, model: DatasetModel) -> str | None:
    """Return a message when a required category value is not present in the data."""
    for role, needed in metric.requires_values.items():
        if role not in model.roles:
            return f"Missing role: {role}"
        present = set(column_categories(model, model.roles[role]) or [])
        missing = [value for value in needed if value not in present]
        if missing:
            return f"Column '{model.roles[role]}' has no value {', '.join(missing)}"
    return None


def check_metric(metric: Metric, model: DatasetModel) -> str | None:
    """Return why a metric is unavailable, or None if it can be computed."""
    for requirement in metric.requires:
        problem = requirement_problem(requirement, model)
        if problem:
            return problem
    return value_problem(metric, model)


def evaluate_metric(metric: Metric, model: DatasetModel) -> MetricStatus:
    """Decide availability of one metric, applying its fallback expression when useful."""
    problem = check_metric(metric, model)
    if problem is None:
        return MetricStatus(metric=metric, available=True)
    if metric.fallback_expr:
        fallback = metric.model_copy(
            update={
                "expr": metric.fallback_expr,
                "requires": placeholders(metric.fallback_expr),
                "requires_values": {},
                "fallback_expr": None,
            }
        )
        if check_metric(fallback, model) is None:
            return MetricStatus(metric=fallback, available=True, reason=problem)
    return MetricStatus(metric=metric, available=False, reason=problem)


def validate_expression(
    con: duckdb.DuckDBPyConnection, model: DatasetModel, expr: str
) -> str:
    """Check a user-written metric expression; return it unchanged or raise ValidationError.

    The expression must be one aggregate expression over ``data`` (no comments, semicolons,
    FROM, WHERE or set operations), must use known roles and must run on the real data.
    """
    text = expr.strip()
    if not text or len(text) > MAX_EXPRESSION_LENGTH:
        raise ValidationError(f"The formula must be 1 to {MAX_EXPRESSION_LENGTH} characters long.")
    if any(token in text for token in (";", "--", "/*", "*/")):
        raise ValidationError("The formula may not contain ';' or comments.")
    metric = Metric(id="custom", label="Custom", expr=text)
    select_sql = f"SELECT {metric_sql(metric, model.roles)} AS value"
    try:
        tree = sqlglot.parse_one(select_sql, dialect="duckdb")
    except sqlglot.errors.SqlglotError as exc:
        raise ValidationError(f"The formula could not be read: {str(exc)[:200]}") from exc
    extra_parts = {key for key, value in tree.args.items() if value and key != "expressions"}
    if not isinstance(tree, exp.Select) or extra_parts or len(tree.expressions) != 1:
        raise ValidationError("The formula must be a single expression such as SUM({amount}).")
    if tree.find(exp.AggFunc) is None:
        raise ValidationError("The formula must aggregate values, for example with SUM or AVG.")
    execute_query(con, f"{select_sql} FROM data")
    return text


def apply_overrides(pack: MetricPack, overrides: dict[str, str] | None) -> MetricPack:
    """Replace the expression of metrics the user edited in the UI."""
    if not overrides:
        return pack
    metrics = [
        m.model_copy(update={"expr": overrides[m.id], "fallback_expr": None})
        if m.id in overrides else m
        for m in pack.metrics
    ]
    return pack.model_copy(update={"metrics": metrics})


def evaluate_pack(
    model: DatasetModel, pack_name: str | None = None, overrides: dict[str, str] | None = None
) -> list[MetricStatus]:
    """Availability of every metric of a pack, primary metric first."""
    pack = apply_overrides(load_pack(pack_name or choose_pack(model)), overrides)
    statuses = [evaluate_metric(metric, model) for metric in pack.metrics]
    return sorted(statuses, key=lambda s: s.metric.id != pack.primary_metric)


def available_metrics(
    model: DatasetModel, pack_name: str | None = None, overrides: dict[str, str] | None = None
) -> list[Metric]:
    """Metrics that can be computed on this dataset (primary metric first when available)."""
    return [s.metric for s in evaluate_pack(model, pack_name, overrides) if s.available]


def unavailable_metrics(
    model: DatasetModel, pack_name: str | None = None, overrides: dict[str, str] | None = None
) -> list[MetricStatus]:
    """Metrics that cannot be computed, each with the reason."""
    return [s for s in evaluate_pack(model, pack_name, overrides) if not s.available]


def primary_metric(
    model: DatasetModel, pack_name: str | None = None, overrides: dict[str, str] | None = None
) -> Metric | None:
    """The pack's primary metric, else its first available metric, else generic ``row_count``."""
    name = pack_name or choose_pack(model)
    available = available_metrics(model, name, overrides)
    for metric in available:
        if metric.id == load_pack(name).primary_metric:
            return metric
    if available:
        return available[0]
    generic = available_metrics(model, "generic", overrides)
    return generic[0] if generic else None
