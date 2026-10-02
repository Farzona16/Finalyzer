"""Datasets (`/api/v1/datasets/...`) and the demo loader (`/api/v1/demo/...`)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, File, Form, Request, Response, UploadFile
from sqlalchemy.orm import Session

from app.config import BackendSettings
from app.deps import (
    dataset_of,
    get_app_settings,
    get_db,
    get_jobs,
    get_services,
    limit,
    require,
)
from app.errors import AppError, locale_of
from app.jobs.runner import JobRunner
from app.models import Dataset, User
from app.routers.jobs import job_out
from app.schemas.datasets import DatasetOut, DemoOut, JobOut, ModelPatchIn, ProfileOut
from app.security.permissions import Permission
from app.services import datasets as service
from app.services.datasets import Services
from app.services.uploads import display_name, validate_upload
from core.ingest.demo import DEMO_DIR, DEMO_NAMES

router = APIRouter(prefix="/datasets", tags=["datasets"])
demo_router = APIRouter(prefix="/demo", tags=["demo"])
CHUNK = 1024 * 1024


def dataset_out(dataset: Dataset) -> DatasetOut:
    """List item / detail view of a dataset."""
    stored = service.stored_model(dataset)
    return DatasetOut(
        id=dataset.id,
        name=dataset.name,
        status=dataset.status,  # type: ignore[arg-type]
        domain=dataset.domain,
        row_count=dataset.row_count,
        tables=stored.row_counts,
        created_at=dataset.created_at,
        last_accessed_at=dataset.last_accessed_at,
    )


def profile_out(dataset: Dataset) -> ProfileOut:
    """The summary-screen view: tables, columns, joins, roles, cleaning, removed PII."""
    stored = service.stored_model(dataset)
    built = stored.built
    return ProfileOut(
        dataset=dataset_out(dataset),
        fact_table=stored.fact_table,
        tables=stored.profiles,
        columns=built.columns if built else [],
        relationships=stored.relationships,
        proposed_relationships=stored.proposed,
        roles=built.roles if built else {},
        roles_automatic=stored.roles is None,
        dropped_pii=built.dropped_pii if built else [],
        cleaning_report=stored.cleaning_report,
        warnings=stored.warnings,
        domain=dataset.domain,
    )


async def read_limited(upload: UploadFile, limit_bytes: int) -> bytes:
    """Read an uploaded file, stopping one byte past the limit."""
    parts: list[bytes] = []
    size = 0
    while chunk := await upload.read(CHUNK):
        size += len(chunk)
        if size > limit_bytes:
            raise AppError("payload_too_large")
        parts.append(chunk)
    return b"".join(parts)


@router.post("", response_model=JobOut, status_code=202)
async def upload_files(
    request: Request,
    files: list[UploadFile] = File(...),
    name: str | None = Form(None, max_length=160),
    user: User = Depends(require(Permission.UPLOAD)),
    _limit: None = Depends(limit("upload")),
    settings: BackendSettings = Depends(get_app_settings),
    jobs: JobRunner = Depends(get_jobs),
    services: Services = Depends(get_services),
) -> JobOut:
    """Validate the files (S9) and start the ingest job."""
    checked: list[tuple[str, bytes]] = []
    for upload in files:
        filename = (upload.filename or "").strip()
        data = await read_limited(upload, settings.max_upload_mb * 1024 * 1024)
        if not filename or not data.strip():
            continue  # extra empty multipart parts from the browser
        validate_upload(filename, data, settings)
        checked.append((filename, data))
    if not checked or len(checked) > settings.max_upload_files:
        raise AppError("validation_failed", extra={"max_files": settings.max_upload_files})
    label = (name or "").strip() or display_name(checked[0][0])
    job = jobs.submit(
        user.workspace_id,
        user.id,
        "ingest",
        service.make_upload_job(
            services, checked, workspace_id=user.workspace_id, user_id=user.id, name=label
        ),
    )
    return job_out(job, locale_of(request))


@router.get("", response_model=list[DatasetOut])
def list_datasets(
    user: User = Depends(require(Permission.READ)), db: Session = Depends(get_db)
) -> list[DatasetOut]:
    """Datasets of the workspace, newest first."""
    return [dataset_out(row) for row in service.list_datasets(db, user.workspace_id)]


@router.get("/{dataset_id}", response_model=DatasetOut)
def get_dataset(dataset: Dataset = Depends(dataset_of(Permission.READ))) -> DatasetOut:
    """One dataset."""
    return dataset_out(dataset)


@router.delete("/{dataset_id}", status_code=204)
def delete_dataset(
    dataset: Dataset = Depends(dataset_of(Permission.UPLOAD)),
    user: User = Depends(require(Permission.UPLOAD)),
    db: Session = Depends(get_db),
    services: Services = Depends(get_services),
) -> Response:
    """Hard delete: files, caches and rows (S17)."""
    service.delete_dataset(services, db, dataset, reason="user", actor_id=user.id)
    return Response(status_code=204)


@router.get("/{dataset_id}/profile", response_model=ProfileOut)
def get_profile(dataset: Dataset = Depends(dataset_of(Permission.READ))) -> ProfileOut:
    """Tables, columns, detected joins and roles, cleaning report, removed personal data."""
    return profile_out(dataset)


@router.patch("/{dataset_id}/model", response_model=ProfileOut)
def patch_model(
    body: ModelPatchIn,
    dataset: Dataset = Depends(dataset_of(Permission.UPLOAD)),
    db: Session = Depends(get_db),
    services: Services = Depends(get_services),
) -> ProfileOut:
    """Change the fact table, joins, column roles or the domain; the flat view is rebuilt."""
    return profile_out(service.apply_model_patch(services, dataset, body, db))


@router.post("/{dataset_id}/build", response_model=JobOut, status_code=202)
def build(
    request: Request,
    dataset: Dataset = Depends(dataset_of(Permission.UPLOAD)),
    user: User = Depends(require(Permission.UPLOAD)),
    db: Session = Depends(get_db),
    jobs: JobRunner = Depends(get_jobs),
    services: Services = Depends(get_services),
) -> JobOut:
    """Finish the dataset (the KPI autopilot joins this job in phase U5)."""
    function = service.start_build(services, dataset, db)
    job = jobs.submit(user.workspace_id, user.id, "build", function)
    return job_out(job, locale_of(request))


@demo_router.get("", response_model=list[DemoOut])
def list_demos(_user: User = Depends(require(Permission.READ))) -> list[DemoOut]:
    """The built-in demo datasets."""
    return [
        DemoOut(
            name=name, domain=name, tables=sorted(p.stem for p in (DEMO_DIR / name).glob("*.csv"))
        )
        for name in DEMO_NAMES
    ]


@demo_router.post("/{name}/load", response_model=JobOut, status_code=202)
def load_demo(
    name: str,
    request: Request,
    user: User = Depends(require(Permission.UPLOAD)),
    _limit: None = Depends(limit("upload")),
    jobs: JobRunner = Depends(get_jobs),
    services: Services = Depends(get_services),
) -> JobOut:
    """Load a demo dataset through the same pipeline as an upload."""
    if name not in DEMO_NAMES:
        raise AppError("not_found")
    job = jobs.submit(
        user.workspace_id,
        user.id,
        "ingest",
        service.make_demo_job(services, name, workspace_id=user.workspace_id, user_id=user.id),
    )
    return job_out(job, locale_of(request))
