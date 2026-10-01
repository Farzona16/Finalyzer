"""False-positive guard: stable data with noise and no planted pattern gives almost no findings."""

from __future__ import annotations

from datetime import date, timedelta

import numpy as np
import pandas as pd

from core.config import Settings
from core.ingest.flatten import (
    build_dataset,
    pick_fact_table,
    propose,
    workspace_from_raw,
)
from core.insights.engine import run_detectors
from core.metrics.registry import available_metrics, primary_metric
from tests.helpers import DemoDataset

CATEGORIES = ["Books", "Toys", "Garden", "Kitchen", "Sports", "Music"]
CHANNELS = ["online", "in-store"]
START = date(2025, 1, 1)
END = date(2026, 9, 30)


def boring_sales(seed: int = 7) -> pd.DataFrame:
    """Two years of evenly spread sales: same volume every day, small random noise."""
    rng = np.random.default_rng(seed)
    days = (END - START).days + 1
    rows = []
    order = 0
    for offset in range(days):
        day = START + timedelta(days=offset)
        for _ in range(60):
            order += 1
            rows.append(
                (
                    f"O{order}", day.isoformat(), f"C{rng.integers(1, 1200)}",
                    CATEGORIES[rng.integers(0, len(CATEGORIES))],
                    CHANNELS[rng.integers(0, len(CHANNELS))],
                    str(int(rng.integers(1, 4))), f"{rng.uniform(40, 60):.2f}",
                )
            )
    return pd.DataFrame(
        rows,
        columns=["order_id", "sale_date", "customer_id", "category", "channel", "quantity",
                 "amount"],
    )


def build_boring() -> DemoDataset:
    """Load the boring table through the real ingestion code."""
    workspace = workspace_from_raw({"sales": boring_sales()})
    fact = pick_fact_table(workspace)
    model = build_dataset(workspace, fact, propose(workspace, fact))
    return DemoDataset("boring", workspace, model)


def test_boring_dataset_gives_at_most_one_finding() -> None:
    demo = build_boring()
    settings = Settings(_env_file=None)
    report = run_detectors(
        demo.con, demo.model, available_metrics(demo.model), primary_metric(demo.model), settings
    )
    assert {"date", "amount", "customer_id"} <= set(demo.model.roles)
    titles = [f"{f.type}: {f.title}" for f in report.findings]
    print("\nboring dataset findings:", titles)
    assert report.notes == []
    assert len(report.findings) <= 1, titles


def test_short_history_skips_time_detectors_with_a_note() -> None:
    frame = boring_sales()
    frame = frame[frame["sale_date"] < "2025-02-01"]
    workspace = workspace_from_raw({"sales": frame})
    fact = pick_fact_table(workspace)
    model = build_dataset(workspace, fact, propose(workspace, fact))
    settings = Settings(_env_file=None)
    report = run_detectors(
        workspace.con, model, available_metrics(model), primary_metric(model), settings
    )
    assert not [f for f in report.findings if f.type in ("trend", "driver", "segment_momentum")]
    assert any("month" in note.lower() for note in report.notes), report.notes
