"""Request and response models for datasets, jobs and demos (plus the stored model)."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field
from pydantic import StringConstraints as Str

from app.schemas.auth import StrictModel
from core.models import CleaningAction, ColumnInfo, DatasetModel, Relationship

DOMAINS = ("retail", "bank", "sales", "statistics", "generic", "project", "expenses")
Domain = Literal["retail", "bank", "sales", "statistics", "generic", "project", "expenses"]
Identifier = Annotated[str, Str(min_length=1, max_length=128)]


class KpiPref(BaseModel):
    """What the user changed about one KPI in the KPI manager."""

    enabled: bool | None = None
    label: dict[str, str] = Field(default_factory=dict)  # language -> name
    order: int | None = None


class StoredModel(BaseModel):
    """Everything about a dataset that is kept in ``datasets.model_json``."""

    model_config = ConfigDict(extra="ignore")

    tables: list[str]  # table names in stored-file order (t0, t1, ...)
    profiles: dict[str, list[ColumnInfo]]
    row_counts: dict[str, int]
    cleaning_report: list[CleaningAction] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    fact_table: str
    proposed: list[Relationship] = Field(default_factory=list)  # every suggested join
    relationships: list[Relationship] = Field(default_factory=list)  # the joins in use
    roles: dict[str, str] | None = None  # None = detected automatically
    built: DatasetModel | None = None
    # U5: the KPI autopilot result, who chose the domain, and the user's KPI manager edits.
    autopilot: dict[str, Any] | None = None
    domain_source: Literal["auto", "user"] = "auto"
    kpi_prefs: dict[str, KpiPref] = Field(default_factory=dict)


class JobError(BaseModel):
    """A failed job: a stable code plus localized texts."""

    code: str
    title: str
    detail: str
    reason: str | None = None  # English technical reason, shown in the Expert view only


class JobOut(BaseModel):
    """State of a background job."""

    id: str
    kind: str
    status: Literal["queued", "running", "done", "failed"]
    progress: int
    message_key: str
    result: dict[str, Any] | None = None
    error: JobError | None = None
    created_at: datetime
    updated_at: datetime


class DatasetOut(BaseModel):
    """List item and detail of a dataset."""

    id: str
    name: str
    status: Literal["profiled", "building", "ready", "failed"]
    domain: str | None
    row_count: int
    tables: dict[str, int]
    created_at: datetime
    last_accessed_at: datetime


class RelationshipIn(StrictModel):
    """One join chosen by the user."""

    fact_col: Identifier
    dim_table: Identifier
    dim_col: Identifier


class ModelPatchIn(StrictModel):
    """Changes to the dataset model (Advanced editor). Omitted fields stay as they are."""

    fact_table: Identifier | None = None
    relationships: list[RelationshipIn] | None = None
    roles: dict[Identifier, Identifier] | None = None
    domain: Domain | None = None


class ProfileOut(BaseModel):
    """What the summary screen shows."""

    dataset: DatasetOut
    fact_table: str
    tables: dict[str, list[ColumnInfo]]
    relationships: list[Relationship]
    proposed_relationships: list[Relationship]
    roles: dict[str, str]
    roles_automatic: bool
    dropped_pii: list[str]
    cleaning_report: list[CleaningAction]
    warnings: list[str]
    domain: str | None


class DemoOut(BaseModel):
    """A built-in demo dataset."""

    name: str
    domain: str
    tables: list[str]


KpiLabel = Annotated[str, Str(strip_whitespace=True, min_length=1, max_length=100,
                              pattern=r"^[^\x00-\x1f\x7f]+$")]


class KpiPatchIn(StrictModel):
    """KPI manager edit: show or hide, rename (per language), move."""

    enabled: bool | None = None
    label: dict[Literal["uz", "en"], KpiLabel] | None = None
    order: int | None = Field(default=None, ge=0, le=1000)


class DomainOut(BaseModel):
    """The detected (or chosen) domain with a plain-language explanation."""

    pack: str
    label: str
    headline: str
    confidence: float
    level: Literal["high", "medium", "low"]
    source: Literal["scores", "tiebreak", "user"]
    reasons: list[str]
    alternatives: list[dict[str, str]]


class KpiOut(BaseModel):
    """One metric in the KPI manager and, when it is a tile, on the dashboard."""

    id: str
    label: str
    short: str
    description: str
    unit: str
    good_direction: str
    source: str
    ai_translated: bool
    enabled: bool
    is_tile: bool
    order: int | None
    value: float | None
    display: str
    previous: float | None = None
    delta: float | None = None
    delta_kind: str | None = None
    delta_display: str = ""
    tone: str = "neutral"
    expert: dict[str, Any] | None = None  # definition and SQL (analyst and admin only)


class KpiListOut(BaseModel):
    """GET /datasets/{id}/kpis."""

    domain: DomainOut | None
    tiles: list[str]
    items: list[KpiOut]
    period: dict[str, str] | None = None  # {"start", "end"} the tiles describe, when known
