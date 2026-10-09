"""Shared fixtures: the two demo datasets built with the real ingestion code."""

from __future__ import annotations

import pytest

from core.config import Settings
from core.insights.engine import run_detectors
from core.metrics.registry import available_metrics, primary_metric
from core.models import Finding
from tests.helpers import DemoDataset, build_demo

# The developer's .env (keys, model names, candidate counts) must never change test outcomes.
Settings.model_config["env_file"] = None


@pytest.fixture(scope="session")
def retail() -> DemoDataset:
    """Retail demo dataset (shared by all tests; do not leave filters applied)."""
    return build_demo("retail")


@pytest.fixture(scope="session")
def retail_findings(retail: DemoDataset) -> list[Finding]:
    """Ranked findings of the retail demo (detectors run once per test session)."""
    metrics = available_metrics(retail.model)
    settings = Settings(_env_file=None)
    report = run_detectors(
        retail.con, retail.model, metrics, primary_metric(retail.model), settings
    )
    return report.findings


@pytest.fixture(scope="session")
def bank() -> DemoDataset:
    """Bank demo dataset (shared by all tests; do not leave filters applied)."""
    return build_demo("bank")


@pytest.fixture(scope="session")
def sales() -> DemoDataset:
    """Sales (CRM) demo dataset."""
    return build_demo("sales")


@pytest.fixture(scope="session")
def statistics() -> DemoDataset:
    """Statistics (manufacturing quality) demo dataset."""
    return build_demo("statistics")
