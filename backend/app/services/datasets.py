"""Dataset life cycle: ingest, model edits, build, delete and the idle/retention sweep.

Every query is scoped by ``workspace_id`` (S6): a dataset of another workspace is "not found".
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Callable
from datetime import datetime, timedelta

import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app import clock
from app.config import BackendSettings
from app.db import session_scope
from app.errors import AppError
from app.jobs.runner import JobContext, JobFailure
from app.models import Dataset, User, Workspace
from app.schemas.datasets import ModelPatchIn, StoredModel
from app.security import audit_chain
from app.services.engine import core_settings
from app.storage.handles import HandleCache
from app.storage.store import DatasetStore
from core.errors import UserFacingError
from core.ingest.cleaner import clean_tables
from core.ingest.flatten import (
    build_dataset,
    open_workspace,
    pick_fact_table,
    propose,
)
from core.llm.audit import AuditLog
from core.llm.registry import LLMRuntime
from core.models import Relationship
from core.runtime.profile import load_limits

logger = logging.getLogger("finalyzer.datasets")
MIN_CONTAINMENT = 0.95  # joins at least this complete are pre-confirmed (spec 9.3)
TOUCH_EVERY = timedelta(seconds=30)  # how often "last accessed" is written


class Services:
    """The long-lived objects the dataset code needs (one per app)."""

    def __init__(
        self,
        settings: BackendSettings,
        factory: sessionmaker[Session],
        store: DatasetStore,
        handles: HandleCache,
    ) -> None:
        self.settings = settings
        self.factory = factory
        self.store = store
        self.handles = handles
        self.audit = AuditLog()
        self.llm_runtime = LLMRuntime()
        # Startup default from KILL_SWITCH_DEFAULT; a value stored in the database wins (lifespan).
        self.kill_switch = core_settings(settings).kill_switch_default == "on"


# ---------------------------------------------------------------------- lookups


def get_dataset(db: Session, workspace_id: str, dataset_id: str) -> Dataset:
    """The dataset, or "not found" when it does not exist in this workspace."""
    dataset = db.scalar(
        select(Dataset).where(Dataset.id == dataset_id, Dataset.workspace_id == workspace_id)
    )
    if dataset is None:
        raise AppError("not_found")
    return dataset


def list_datasets(db: Session, workspace_id: str) -> list[Dataset]:
    """All datasets of the workspace, newest first."""
    return list(
        db.scalars(
            select(Dataset)
            .where(Dataset.workspace_id == workspace_id)
            .order_by(Dataset.created_at.desc())
        )
    )


def stored_model(dataset: Dataset) -> StoredModel:
    """Parse ``model_json``."""
    return StoredModel.model_validate_json(dataset.model_json)


def touch(db: Session, dataset: Dataset, now: datetime | None = None) -> None:
    """Remember that the dataset is in use (idle purge), without a write on every request."""
    now = now or clock.now()
    if now - clock.as_utc(dataset.last_accessed_at) >= TOUCH_EVERY:
        dataset.last_accessed_at = now
        db.commit()


# ---------------------------------------------------------------------- ingest


def ingest_tables(
    services: Services,
    raw_tables: dict[str, pd.DataFrame],
    *,
    workspace_id: str,
    user_id: str,
    name: str,
    ctx: JobContext,
    audit_detail: dict[str, object] | None = None,
    origin: str = "real",
    extra_warnings: list[str] | None = None,
) -> dict[str, object]:
    """Clean, profile, store and register tables as a new dataset. Runs inside a job."""
    ctx.progress(25, "jobs.cleaning")
    cleaned, actions, warnings = clean_tables(raw_tables)
    if extra_warnings:
        warnings = list(warnings) + list(extra_warnings)
    ctx.progress(45, "jobs.profiling")
    settings = core_settings(services.settings)
    workspace = open_workspace(cleaned, actions, warnings, settings)
    try:
        fact = pick_fact_table(workspace)
        proposed = propose(workspace, fact)
        chosen = [r for r in proposed if r.containment >= MIN_CONTAINMENT]
        ctx.progress(65, "jobs.modeling")
        built = build_dataset(workspace, fact, chosen)
        sample_fraction = 1.0
        kept: list[str] = []
        for note in warnings:
            if note.startswith("sampled:"):
                try:
                    sample_fraction = min(sample_fraction, float(note.split(":", 1)[1]))
                    continue
                except ValueError:
                    pass
            kept.append(note)
        warnings = kept
        if sample_fraction < 0.999:
            pct = max(1, round(sample_fraction * 100))
            warnings.append(f"Based on a sample ({pct}%)")
            built.warnings = list(built.warnings) + [f"Based on a sample ({pct}%)"]
        stored = StoredModel(
            tables=list(cleaned),
            profiles=workspace.profiles,
            row_counts=workspace.row_counts,
            cleaning_report=actions,
            warnings=warnings,
            fact_table=fact,
            proposed=proposed,
            relationships=chosen,
            built=built,
            origin=origin,
            sample_fraction=sample_fraction,
        )
    finally:
        workspace.con.close()
    ctx.progress(80, "jobs.saving")
    storage_name = uuid.uuid4().hex
    services.store.save(storage_name, cleaned)
    try:
        with session_scope(services.factory) as db:
            dataset = Dataset(
                workspace_id=workspace_id,
                name=name,
                status="profiled",
                storage_name=storage_name,
                row_count=built.row_count,
                model_json=stored.model_dump_json(),
                created_by=user_id,
            )
            db.add(dataset)
            db.flush()
            dataset_id = dataset.id
        with session_scope(services.factory) as db:
            audit_chain.record_event(
                db,
                audit_chain.UPLOAD,
                workspace_id=workspace_id,
                actor_user_id=user_id,
                detail={
                    "dataset_id": dataset_id,
                    "rows": built.row_count,
                    "tables": len(cleaned),
                    **(audit_detail or {}),
                },
            )
    except Exception:
        services.store.delete(storage_name)
        raise
    return {"dataset_id": dataset_id}


def make_upload_job(
    services: Services,
    files: list[tuple[str, bytes]],
    *,
    workspace_id: str,
    user_id: str,
    name: str,
) -> Callable[[JobContext], dict[str, object]]:
    """The job function for an upload of validated files."""
    from core.ingest.loader import load_files

    def job(ctx: JobContext) -> dict[str, object]:
        ctx.progress(10, "jobs.reading")
        notes: list[str] = []
        raw = load_files(files, core_settings(services.settings), warnings=notes)
        return ingest_tables(
            services,
            raw,
            workspace_id=workspace_id,
            user_id=user_id,
            name=name,
            ctx=ctx,
            audit_detail={"files": len(files)},
            extra_warnings=notes,
        )

    return job


def make_demo_job(
    services: Services, demo: str, *, workspace_id: str, user_id: str
) -> Callable[[JobContext], dict[str, object]]:
    """The job function that loads a built-in demo dataset through the normal pipeline."""
    from core.ingest.demo import DEMO_DIR
    from core.ingest.loader import load_folder

    def job(ctx: JobContext) -> dict[str, object]:
        ctx.progress(10, "jobs.reading")
        raw = load_folder(DEMO_DIR / demo, core_settings(services.settings))
        return ingest_tables(
            services,
            raw,
            workspace_id=workspace_id,
            user_id=user_id,
            name=demo,
            ctx=ctx,
            audit_detail={"demo": demo},
            origin="synthetic",
        )

    return job


# ---------------------------------------------------------------------- model edits and build


def _chosen_joins(stored: StoredModel, patch: ModelPatchIn) -> list[Relationship]:
    """The joins named in a patch. Known proposals keep their measured containment."""
    known = {(r.fact_col, r.dim_table, r.dim_col): r for r in stored.proposed}
    joins = []
    for item in patch.relationships or []:
        key = (item.fact_col, item.dim_table, item.dim_col)
        joins.append(known.get(key) or Relationship(**item.model_dump(), containment=0.0))
    return joins


def apply_model_patch(
    services: Services, dataset: Dataset, patch: ModelPatchIn, db: Session
) -> Dataset:
    """Rebuild the flat view with the user's choices and store the result."""
    if dataset.status == "building":
        raise AppError("dataset_busy")
    stored = stored_model(dataset)
    fact = patch.fact_table or stored.fact_table
    if fact not in stored.row_counts:
        raise AppError("model_invalid", params={"why": "unknown_table"})
    fact_changed = fact != stored.fact_table
    candidate = stored.model_copy(
        update={
            "fact_table": fact,
            "relationships": (
                _chosen_joins(stored, patch)
                if patch.relationships is not None
                else ([] if fact_changed else list(stored.relationships))
            ),
            "roles": patch.roles if patch.roles is not None else stored.roles,
        }
    )
    services.handles.drop(dataset.id)
    try:
        with services.handles.use(dataset.id, dataset.storage_name, candidate) as handle:
            if fact_changed and patch.relationships is None:
                # A new fact table: suggest its joins the same way the first upload did.
                candidate.proposed = propose(handle.workspace, fact)
                candidate.relationships = [
                    r for r in candidate.proposed if r.containment >= MIN_CONTAINMENT
                ]
                handle.model = build_dataset(
                    handle.workspace, fact, candidate.relationships, candidate.roles
                )
            candidate.built = handle.model
    except UserFacingError as error:
        services.handles.drop(dataset.id)
        raise AppError("model_invalid", extra={"reason": error.message}) from error
    if patch.domain is not None:
        dataset.domain = patch.domain
        candidate.domain_source = "user"
    candidate.autopilot = None  # stale until the next build; the user's KPI edits are kept
    candidate.insight_cache_key = ""
    candidate.insight_payload = None
    dataset.model_json = candidate.model_dump_json()
    dataset.row_count = candidate.built.row_count if candidate.built else dataset.row_count
    dataset.status = "profiled"
    db.commit()
    return dataset


def start_build(
    services: Services, dataset: Dataset, db: Session
) -> Callable[[JobContext], dict[str, object]]:
    """Mark the dataset as building and return the job that finishes it."""
    if dataset.status == "building":
        raise AppError("dataset_busy")
    dataset.status = "building"
    db.commit()
    dataset_id, workspace_id = dataset.id, dataset.workspace_id
    from app.services import kpis as kpi_service  # late import: kpis needs this module

    def job(ctx: JobContext) -> dict[str, object]:
        ctx.progress(20, "jobs.building")
        try:
            with session_scope(services.factory) as session:
                row = session.get(Dataset, dataset_id)
                if row is None:
                    raise JobFailure("not_found")
                model = stored_model(row)
                with services.handles.use(row.id, row.storage_name, model) as handle:
                    model.built = handle.model
                    ctx.progress(55, "jobs.autopilot")
                    kpi_service.run_for_build(
                        services, row, model, handle.model, handle.workspace.con
                    )
                    model.insight_cache_key = ""
                    model.insight_payload = None
                row.model_json = model.model_dump_json()
                row.status = "ready"
                if (
                    services.settings.app_env != "test"
                    and load_limits().insight_precompute == "after_build"
                ):
                    ctx.progress(80, "jobs.insights")
                    from app.services.insights import run_insights

                    run_insights(services, row, "en", "simple", session)
        except Exception:
            with session_scope(services.factory) as session:
                row = session.get(Dataset, dataset_id)
                if row is not None:
                    row.status = "failed"
            raise
        return {"dataset_id": dataset_id, "workspace_id": workspace_id}

    return job


# ---------------------------------------------------------------------- delete and purge


def delete_dataset(
    services: Services, db: Session, dataset: Dataset, *, reason: str, actor_id: str | None
) -> None:
    """Hard delete (S17): handle, files, then the database rows, then an audit event."""
    dataset_id, workspace_id, storage_name = dataset.id, dataset.workspace_id, dataset.storage_name
    services.handles.drop(dataset_id)
    services.store.delete(storage_name)
    db.delete(dataset)
    db.commit()
    audit_chain.record_event(
        db,
        audit_chain.DELETE,
        workspace_id=workspace_id,
        actor_user_id=actor_id,
        detail={"dataset_id": dataset_id, "reason": reason},
    )


def purge_for_logout(services: Services, db: Session, user: User) -> int:
    """Ephemeral mode: a user's datasets disappear when the user signs out."""
    if services.store.persistent:
        return 0
    rows = db.scalars(
        select(Dataset).where(
            Dataset.workspace_id == user.workspace_id, Dataset.created_by == user.id
        )
    ).all()
    for row in rows:
        delete_dataset(services, db, row, reason="logout", actor_id=user.id)
    return len(rows)


def purge_expired(services: Services, db: Session, now: datetime | None = None) -> int:
    """Delete idle ephemeral datasets, datasets past retention, and files without a record."""
    now = now or clock.now()
    removed = 0
    if not services.store.persistent:
        cutoff = now - timedelta(minutes=services.settings.session_idle_minutes)
        for row in db.scalars(select(Dataset)):
            if clock.as_utc(row.last_accessed_at) < cutoff and row.status != "building":
                delete_dataset(services, db, row, reason="idle", actor_id=None)
                removed += 1
    else:
        for workspace in db.scalars(select(Workspace).where(Workspace.retention_days > 0)):
            cutoff = now - timedelta(days=workspace.retention_days or 0)
            for row in db.scalars(select(Dataset).where(Dataset.workspace_id == workspace.id)):
                if clock.as_utc(row.created_at) < cutoff and row.status != "building":
                    delete_dataset(services, db, row, reason="retention", actor_id=None)
                    removed += 1
    removed += remove_orphans(services, db)
    return removed


def remove_orphans(services: Services, db: Session) -> int:
    """Delete folders on disk that no dataset row points to (left by a crash)."""
    known = set(db.scalars(select(Dataset.storage_name)))
    orphans = [name for name in services.store.stored_names() if name not in known]
    for name in orphans:
        services.store.delete(name)
    return len(orphans)


def reset_after_restart(services: Services, db: Session) -> None:
    """Start-up housekeeping.

    Ephemeral data never survives a restart, so its rows and files go. In both modes a dataset
    left in ``building`` by a crash is marked failed so that it can be built again.
    """
    from app.models import Job

    for job in db.scalars(select(Job).where(Job.status.in_(("queued", "running")))):
        job.status = "interrupted"
        job.message_key = "jobs.interrupted"
    if not services.store.persistent:
        services.store.wipe()
        for row in db.scalars(select(Dataset)):
            db.delete(row)
        db.commit()
        return
    for row in db.scalars(select(Dataset).where(Dataset.status == "building")):
        row.status = "failed"
    db.commit()
    remove_orphans(services, db)
