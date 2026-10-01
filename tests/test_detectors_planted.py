"""The detectors must find the patterns planted in the demo data (Appendix A)."""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path

import pytest

from core.config import Settings
from core.insights.engine import InsightReport, run_detectors
from core.metrics.registry import available_metrics, primary_metric
from core.models import Finding
from tests.helpers import DemoDataset

REPORT_PATH = Path("reports/planted_patterns.json")
Check = Callable[[list[Finding]], bool]


def has(
    findings: list[Finding], type_: str, metric: str | None = None,
    segment: tuple[str, str] | None = None, text: str | None = None,
) -> bool:
    """True when a finding matches type, metric, segment (key, value) and title text."""
    for f in findings:
        if f.type != type_ or (metric and f.metric_id != metric):
            continue
        if segment and (f.segment or {}).get(segment[0]) != segment[1]:
            continue
        if text and text not in f.title:
            continue
        return True
    return False


def has_column(findings: list[Finding], column: str) -> bool:
    """True when a data-quality finding names the column."""
    return any(f.type == "data_quality" and f.facts.get("column") == column for f in findings)


RETAIL_PATTERNS: dict[str, Check] = {
    "R1 trend: revenue falls in 2026-09": lambda fs: has(fs, "trend", "revenue", text="2026-09"),
    "R1 driver: Store 03": lambda fs: has(fs, "driver", "revenue", ("store_name", "Store 03")),
    "R2 anomaly: spike 2026-08-12": lambda fs: has(fs, "anomaly", text="2026-08-12"),
    "R2 anomaly: drop 2026-07-19": lambda fs: has(fs, "anomaly", text="2026-07-19"),
    "R3 concentration: Electronics": lambda fs: has(
        fs, "concentration", "revenue", ("category", "Electronics")
    ),
    "R4 segment gap: online vs in-store": lambda fs: has(
        fs, "segment_gap", "avg_order_value", ("channel", "online")
    ),
    "R5 churn rise": lambda fs: has(fs, "churn", "churn_rate"),
    "R6 data quality: discount": lambda fs: has_column(fs, "discount"),
    "R7 momentum: Home & Garden": lambda fs: has(
        fs, "segment_momentum", "revenue", ("category", "Home & Garden")
    ),
    "R8 trend: gross margin %": lambda fs: has(fs, "trend", "gross_margin_pct"),
}

BANK_PATTERNS: dict[str, Check] = {
    "B1 trend: volume falls in 2026-09": lambda fs: has(
        fs, "trend", "txn_volume", text="2026-09"
    ),
    "B1 driver: Branch 07": lambda fs: has(
        fs, "driver", "txn_volume", ("branch_name", "Branch 07")
    ),
    "B2 anomaly: burst 2026-08-27": lambda fs: has(fs, "anomaly", text="2026-08-27"),
    "B3 concentration: top-3 branches": lambda fs: has(
        fs, "concentration", "txn_volume", ("branch_name", "Branch 07")
    ),
    "B4 trend: failed rate rises": lambda fs: has(fs, "trend", "failed_rate_pct"),
    "B4 segment gap: mobile": lambda fs: has(
        fs, "segment_gap", "failed_rate_pct", ("channel", "mobile")
    ),
    "B5 churn rise": lambda fs: has(fs, "churn", "churn_rate"),
    "B6 data quality: fee": lambda fs: has_column(fs, "fee"),
    "B7 momentum: mobile": lambda fs: has(
        fs, "segment_momentum", "txn_volume", ("channel", "mobile")
    ),
    "B8 segment gap: premium vs standard": lambda fs: has(
        fs, "segment_gap", "avg_ticket", ("segment", "premium")
    ),
}

# Ten checks cover the eight patterns of each dataset (R1, R2, B1, B4 have two parts).
PATTERN_GROUPS = {
    "retail": ["R1", "R2", "R3", "R4", "R5", "R6", "R7", "R8"],
    "bank": ["B1", "B2", "B3", "B4", "B5", "B6", "B7", "B8"],
}


def report_for(demo: DemoDataset) -> InsightReport:
    """Run all detectors on a demo dataset with default settings."""
    settings = Settings(_env_file=None)
    metrics = available_metrics(demo.model)
    return run_detectors(demo.con, demo.model, metrics, primary_metric(demo.model), settings)


def evaluate(name: str, patterns: dict[str, Check], findings: list[Finding]) -> dict[str, bool]:
    """Print the table of one dataset and return pattern name -> detected."""
    table = {label: check(findings) for label, check in patterns.items()}
    print(f"\n{name}: planted patterns")
    for label, ok in table.items():
        print(f"  {'yes' if ok else 'NO ':3}  {label}")
    return table


def pattern_ids(table: dict[str, bool], group: list[str]) -> dict[str, bool]:
    """A pattern (R1..R8) counts as found when all of its checks passed."""
    return {p: all(ok for label, ok in table.items() if label.startswith(p)) for p in group}


def save(name: str, table: dict[str, bool]) -> None:
    """Keep the table for the final report."""
    REPORT_PATH.parent.mkdir(exist_ok=True)
    data = json.loads(REPORT_PATH.read_text()) if REPORT_PATH.exists() else {}
    data[name] = table
    REPORT_PATH.write_text(json.dumps(data, indent=2), encoding="utf-8")


@pytest.mark.parametrize("name", ["retail", "bank"])
def test_planted_patterns_are_detected(
    name: str, retail: DemoDataset, bank: DemoDataset
) -> None:
    demo = retail if name == "retail" else bank
    patterns = RETAIL_PATTERNS if name == "retail" else BANK_PATTERNS
    report = report_for(demo)
    table = evaluate(name, patterns, report.findings)
    save(name, table)
    found = pattern_ids(table, PATTERN_GROUPS[name])
    assert sum(found.values()) >= 7, f"only {sum(found.values())}/8 patterns found: {found}"
    for label, ok in table.items():
        assert ok, f"pattern not detected: {label}"


def test_detectors_report_no_failures(retail: DemoDataset, bank: DemoDataset) -> None:
    for demo in (retail, bank):
        assert report_for(demo).notes == []


def test_findings_are_ranked_and_supported(retail: DemoDataset) -> None:
    findings = report_for(retail).findings
    scores = [f.severity * f.confidence for f in findings]
    assert scores == sorted(scores, reverse=True)
    assert all(0 <= f.severity <= 1 and 0 <= f.confidence <= 1 for f in findings)
    assert all(f.support > 0 for f in findings)


def test_last_complete_month_is_reported(retail: DemoDataset) -> None:
    report = report_for(retail)
    assert report.last_complete_month == "2026-09"
    assert report.data_end is not None
