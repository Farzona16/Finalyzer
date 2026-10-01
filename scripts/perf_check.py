"""Time the main steps on a large dataset (default 1,000,000 fact rows).

Usage: ``python scripts/perf_check.py [--rows 1000000] [--json reports/perf.json]``

The retail demo sales table is repeated with new order ids until it reaches the requested
size; dates stay inside the demo period. The script prints one line per step and a summary
that can be copied into KNOWN_LIMITATIONS.md.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from collections.abc import Callable
from datetime import date
from pathlib import Path
from typing import Any

import duckdb
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.config import Settings, load_settings  # noqa: E402
from core.dashboard.chartspecs import (  # noqa: E402
    breakdown_frame,
    default_dimension,
    top_table,
    trend_frame,
)
from core.dashboard.filters import apply_filters, choose_slicers  # noqa: E402
from core.dashboard.kpi import compute_kpis  # noqa: E402
from core.ingest.flatten import (  # noqa: E402
    build_dataset,
    pick_fact_table,
    propose,
    workspace_from_raw,
)
from core.ingest.loader import load_folder  # noqa: E402
from core.insights.engine import run_detectors  # noqa: E402
from core.metrics.registry import available_metrics, primary_metric  # noqa: E402
from core.models import DatasetModel, FilterSpec  # noqa: E402

DEMO_RETAIL = Path("data/demo/retail")
FACT_TABLE = "sales"
ID_COLUMN = "sale_id"
LOAD_STEPS = ("load + clean + profile", "propose + build flat views")
DETECTOR_STEP = "insight detectors"


def enlarge(tables: dict[str, pd.DataFrame], rows: int) -> dict[str, pd.DataFrame]:
    """Repeat the sales table with unique ids until it has about ``rows`` rows."""
    sales = tables[FACT_TABLE]
    copies = max(1, -(-rows // len(sales)))
    parts = []
    for i in range(copies):
        part = sales.copy()
        part[ID_COLUMN] = part[ID_COLUMN].astype(str) + f"{i:03d}"
        parts.append(part)
    return {**tables, FACT_TABLE: pd.concat(parts, ignore_index=True).iloc[:rows]}


def timed(results: dict[str, float], name: str, action: Callable[[], Any]) -> Any:
    """Run ``action``, record its duration in seconds and print it."""
    started = time.perf_counter()
    value = action()
    results[name] = round(time.perf_counter() - started, 3)
    print(f"{name:<40}{results[name]:>9.3f} s", flush=True)
    return value


def dashboard_queries(
    con: duckdb.DuckDBPyConnection,
    model: DatasetModel,
    settings: Settings,
    results: dict[str, float],
    label: str,
) -> None:
    """The queries behind the dashboard, timed under ``label``."""
    metrics = available_metrics(model)
    primary = primary_metric(model)
    spec = FilterSpec()
    timed(results, f"{label}: kpis with previous period",
          lambda: compute_kpis(con, model, metrics, spec, settings))
    timed(results, f"{label}: trend", lambda: trend_frame(con, model, primary, False, settings))
    timed(results, f"{label}: trend + previous year",
          lambda: trend_frame(con, model, primary, True, settings))
    dimension = timed(results, f"{label}: default dimension",
                      lambda: default_dimension(con, model, primary, settings))
    timed(results, f"{label}: breakdown",
          lambda: breakdown_frame(con, model, primary, dimension, settings=settings))
    timed(results, f"{label}: top 10 table",
          lambda: top_table(con, model, primary, dimension, settings))


def time_detectors(
    con: duckdb.DuckDBPyConnection,
    model: DatasetModel,
    settings: Settings,
    results: dict[str, float],
    label: str,
) -> None:
    """Run all insight detectors on the current ``data`` view and print what they found."""
    metrics = available_metrics(model)
    report = timed(results, f"{DETECTOR_STEP}: {label}",
                   lambda: run_detectors(con, model, metrics, primary_metric(model), settings))
    print(f"    findings: {len(report.findings)}, notes: {report.notes}")


def slicer_spec(model: DatasetModel) -> FilterSpec:
    """A typical slicer combination: half a year and one value of the first category slicer."""
    categorical: dict[str, list[str]] = {}
    slicers = choose_slicers(model)
    if slicers:
        info = next(c for c in model.columns if c.name == slicers[0])
        categorical[slicers[0]] = list((info.categories or [])[:2])
    return FilterSpec(date_range=(date(2026, 1, 1), date(2026, 6, 30)), categorical=categorical)


def main() -> None:
    """Build the large dataset and print all timings."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rows", type=int, default=1_000_000)
    parser.add_argument("--json", type=Path, default=None)
    args = parser.parse_args()
    base_settings = load_settings()
    limit = max(args.rows * 2, base_settings.max_rows)
    settings = base_settings.model_copy(update={"max_rows": limit})
    results: dict[str, float] = {}
    raw = enlarge(load_folder(DEMO_RETAIL, settings), args.rows)
    rows = len(raw[FACT_TABLE])
    print(f"fact rows: {rows:,}")
    workspace = timed(results, LOAD_STEPS[0], lambda: workspace_from_raw(raw, settings=settings))
    fact = pick_fact_table(workspace)
    model = timed(
        results, LOAD_STEPS[1],
        lambda: build_dataset(workspace, fact, propose(workspace, fact)),
    )
    dashboard_queries(workspace.con, model, settings, results, "all rows")
    time_detectors(workspace.con, model, settings, results, "all rows")
    spec = slicer_spec(model)
    kept = timed(results, "apply slicers (rebuild view)",
                 lambda: apply_filters(workspace.con, spec, model))
    print(f"rows after slicers: {kept:,}")
    dashboard_queries(workspace.con, model, settings, results, "filtered")
    time_detectors(workspace.con, model, settings, results, "filtered view")
    detector_steps = tuple(k for k in results if k.startswith(DETECTOR_STEP))
    interactive = sum(v for k, v in results.items() if k not in (*LOAD_STEPS, *detector_steps))
    print(f"\nall interactive steps together: {interactive:.2f} s for {rows:,} rows")
    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        payload = {"rows": rows, "seconds": results, "interactive_total_s": round(interactive, 3)}
        args.json.write_text(json.dumps(payload, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
