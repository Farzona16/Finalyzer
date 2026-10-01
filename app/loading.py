"""Load data and build the dataset model. Plain Python (no Streamlit) so it can be tested."""

from __future__ import annotations

import pandas as pd

from core.config import Settings
from core.errors import ValidationError
from core.ingest.demo import DEMO_NAMES
from core.ingest.flatten import Workspace, build_dataset, build_select_sql, workspace_from_raw
from core.ingest.loader import PREVIEW_VIEW, load_files
from core.ingest.profiler import profile_view
from core.ingest.roles import detect_roles
from core.metrics.registry import (
    MetricStatus,
    available_metrics,
    evaluate_pack,
    primary_metric,
)
from core.models import ColumnInfo, DatasetModel, Metric, Relationship

ROLE_ORDER = ("date", "amount", "cost", "quantity", "customer_id", "order_id", "status")


def read_uploads(
    files: list[tuple[str, bytes]], settings: Settings
) -> dict[str, pd.DataFrame]:
    """Parse uploaded files into raw text tables."""
    if not files:
        raise ValidationError("Choose at least one CSV or XLSX file.")
    return load_files(files, settings)


def open_upload(
    raw: dict[str, pd.DataFrame], remove_duplicates: bool, settings: Settings
) -> Workspace:
    """Clean the raw tables (optionally dropping exact duplicates) and open a workspace."""
    return workspace_from_raw(raw, remove_duplicates=remove_duplicates, settings=settings)


def preview_columns(
    workspace: Workspace, fact: str, relationships: list[Relationship]
) -> tuple[list[ColumnInfo], dict[str, str]]:
    """Columns of the future flat view and the roles detected on them.

    A separate view is used so that ``base`` and ``data`` of the built dataset stay untouched.
    """
    sql, _ = build_select_sql(workspace, fact, relationships)
    workspace.con.execute(f"CREATE OR REPLACE VIEW {PREVIEW_VIEW} AS {sql}")
    columns = profile_view(workspace.con, PREVIEW_VIEW)
    return columns, detect_roles(columns)


def build_model(
    workspace: Workspace,
    fact: str,
    relationships: list[Relationship],
    roles: dict[str, str] | None = None,
) -> DatasetModel:
    """Create ``base`` and ``data`` for the chosen fact table, joins and roles."""
    return build_dataset(workspace, fact, relationships, roles)


def order_metrics(
    model: DatasetModel, pack: str | None, overrides: dict[str, str] | None
) -> tuple[list[Metric], Metric | None, list[MetricStatus]]:
    """Available metrics (primary first), the primary metric and the unavailable ones."""
    primary = primary_metric(model, pack, overrides)
    metrics = available_metrics(model, pack, overrides)
    if primary is not None:
        metrics = [primary, *[m for m in metrics if m.id != primary.id]]
    unavailable = [s for s in evaluate_pack(model, pack, overrides) if not s.available]
    return metrics, primary, unavailable


def demo_key(source: str | None) -> str | None:
    """The key of the golden questions for a source (``None`` for uploads)."""
    return source if source in DEMO_NAMES else None
