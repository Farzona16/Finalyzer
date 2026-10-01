"""The two built-in demo datasets: load, clean and build them with the default choices."""

from __future__ import annotations

from pathlib import Path

from core.config import Settings
from core.errors import ValidationError
from core.ingest.flatten import (
    Workspace,
    build_dataset,
    pick_fact_table,
    propose,
    workspace_from_raw,
)
from core.ingest.loader import load_folder
from core.models import DatasetModel

DEMO_DIR = Path(__file__).resolve().parents[2] / "data" / "demo"
DEMO_NAMES = ("retail", "bank", "sales", "statistics")
MIN_CONTAINMENT = 0.95


def load_demo(name: str, settings: Settings | None = None) -> Workspace:
    """Load, clean and profile one demo dataset."""
    if name not in DEMO_NAMES:
        raise ValidationError(f"Unknown demo dataset '{name}'.")
    return workspace_from_raw(load_folder(DEMO_DIR / name, settings), settings=settings)


def build_demo_model(workspace: Workspace) -> DatasetModel:
    """Default build: the automatically chosen fact table and the safe joins."""
    fact = pick_fact_table(workspace)
    joins = [r for r in propose(workspace, fact) if r.containment >= MIN_CONTAINMENT]
    return build_dataset(workspace, fact, joins)
