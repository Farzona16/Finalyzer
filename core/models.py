"""Core data contracts shared by all layers (Section 5 of the specification).

All models allow extra fields so later phases can attach information without breaking
earlier code. Percent-like fields named ``*_pct`` are on a 0..100 scale.
"""

from __future__ import annotations

from datetime import date
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

DType = Literal["integer", "float", "text", "date", "boolean"]
Unit = Literal["currency", "count", "percent", "ratio"]
Level = Literal["High", "Medium", "Low"]


class Base(BaseModel):
    """Common configuration: unknown fields are kept rather than rejected."""

    model_config = ConfigDict(extra="allow")


class ColumnInfo(Base):
    """Profile of one column of the flat view (or of a source table)."""

    name: str
    dtype: DType
    null_pct: float = 0.0
    distinct: int = 0
    is_pii: bool = False
    pii_reason: str | None = None
    role: str | None = None
    categories: list[str] | None = None
    min_value: float | str | None = None
    max_value: float | str | None = None


class CleaningAction(Base):
    """One change made (or one problem found) while cleaning a table."""

    column: str
    action: str
    count: int = 0
    detail: str = ""
    table: str = ""


class Relationship(Base):
    """A proposed or confirmed link from a fact column to a dimension key."""

    fact_col: str
    dim_table: str
    dim_col: str
    containment: float
    cardinality: Literal["many_to_one", "one_to_one"] = "many_to_one"


class DatasetModel(Base):
    """Everything the rest of the app needs to know about the built dataset."""

    dataset_id: str
    fact_table: str
    tables: dict[str, int]
    relationships: list[Relationship] = Field(default_factory=list)
    columns: list[ColumnInfo]
    roles: dict[str, str] = Field(default_factory=dict)
    row_count: int
    dropped_pii: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    cleaning_report: list[CleaningAction] = Field(default_factory=list)
    version: str


class Metric(Base):
    """A business metric written with role placeholders such as ``{amount}``."""

    id: str
    label: str
    expr: str
    requires: list[str] = Field(default_factory=list)
    requires_values: dict[str, list[str]] = Field(default_factory=dict)
    unit: Unit = "count"
    good_direction: Literal["up", "down", "neutral"] = "neutral"
    additive: bool = False
    description: str = ""
    # Used instead of ``expr`` when a value required by ``requires_values`` is not in the data.
    fallback_expr: str | None = None
    # v3: ranking weight (1-100), per-language texts, where the metric came from.
    priority: int = 50
    # {"uz": {"label", "label_gen", "short", "description_plain", "synonyms"}, ...}
    i18n: dict[str, dict[str, Any]] = Field(default_factory=dict)
    source: Literal["pack", "heuristic", "ai", "user"] = "pack"
    ai_translated: bool = False
    spec: dict[str, Any] | None = None


class FilterSpec(Base):
    """Active slicers."""

    date_range: tuple[date, date] | None = None
    categorical: dict[str, list[str]] = Field(default_factory=dict)
    numeric: dict[str, tuple[float, float]] = Field(default_factory=dict)


class QueryResult(Base):
    """Result of one executed query (or of a voted Q&A answer)."""

    sql: str
    columns: list[str]
    rows: list[list[Any]]
    row_count: int
    runtime_ms: float = 0.0
    verified: bool = True
    candidates_total: int = 1
    candidates_agreeing: int = 1
    metric_ids: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class Finding(Base):
    """A verified fact found by a deterministic detector."""

    id: str
    type: str
    metric_id: str
    title: str
    facts: dict[str, float | str]
    segment: dict[str, str] | None = None
    period_label: str = ""
    support: int = 0
    severity: float = 0.0
    confidence: float = 0.0


class RecommendationCard(Base):
    """A readable insight card built from one finding."""

    finding_id: str
    observation: str
    evidence: list[str]
    likely_cause: str
    actions: list[str] = Field(min_length=1, max_length=2)
    expected_impact: str | None = None
    priority: Level
    confidence: Level
    track_metric: str


class AuditEvent(Base):
    """One record of an attempted external call (sent or blocked)."""

    ts: str
    purpose: str
    mode: str
    provider_model: str
    payload_text: str
    payload_bytes: int
    blocked: bool = False
    block_reason: str | None = None
    tokens_in: int = 0
    tokens_out: int = 0
    latency_ms: float = 0.0
    # True when the payload left the machine's private network (counted as "bytes sent outside").
    external: bool = False
    error: str | None = None
