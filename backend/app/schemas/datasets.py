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
