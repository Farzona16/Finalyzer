"""U6: the sales and statistics detectors find what is planted, and stay quiet on noise."""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from core.config import Settings
from core.insights.engine import InsightReport
from core.insights.runner import run_insights
from core.insights.stats_detectors import bh_adjust
from core.models import Finding
from tests.autopilot_fixtures import model_from_frame
from tests.helpers import DemoDataset
from tests.noise_data import noise_measurements, noise_sales

REPORT_PATH = Path("reports/planted_patterns.json")
Check = Callable[[list[Finding]], bool]


def report_for(demo: DemoDataset, pack: str) -> InsightReport:
    return run_insights(demo.con, demo.model, Settings(_env_file=None), pack)


def has(
    findings: list[Finding],
    type_: str,
    metric: str | None = None,
    segment: tuple[str, str] | None = None,
    **facts: str,
) -> bool:
    for f in findings:
        if f.type != type_ or (metric and f.metric_id != metric):
            continue
        if segment and (f.segment or {}).get(segment[0]) != segment[1]:
            continue
        if any(str(f.facts.get(k)) != v for k, v in facts.items()):
            continue
        return True
    return False


SALES_PATTERNS: dict[str, Check] = {
    "S1 trend: win rate falls in 2026-09": lambda fs: any(
        f.type == "trend"
        and f.metric_id == "win_rate_pct"
        and f.facts["last_period"] == "2026-09"
        and f.facts["direction"] == "down"
        for f in fs
    ),
    "S2 win_rate_gap: owner O05 is the weakest": lambda fs: has(
        fs, "win_rate_gap", segment=("owner_id", "O05")
    ),
    "S3 win_rate_gap: referral vs cold_call": lambda fs: has(
        fs, "win_rate_gap", best="referral", worst="cold_call"
    ),
    "S4 concentration: Enterprise": lambda fs: has(
        fs, "concentration", "won_revenue", ("product", "Enterprise")
    ),
    "S5 funnel_drop: Proposal to Negotiation": lambda fs: has(
        fs, "funnel_drop", stage_from="Proposal", stage_to="Negotiation"
    ),
    "S6 stalled: old open pipeline": lambda fs: has(fs, "stalled"),
    "S7 cycle_change: longer": lambda fs: has(fs, "cycle_change", direction="up"),
    "S8 data quality: region": lambda fs: has(fs, "data_quality", column="region"),
}

STATISTICS_PATTERNS: dict[str, Check] = {
    "T1 outliers: thickness_mm": lambda fs: has(fs, "outliers", column="thickness_mm"),
    "T2 correlation: temperature and defects": lambda fs: any(
        f.type == "correlation" and {f.facts["a"], f.facts["b"]} == {"temperature", "defects"}
        for f in fs
    ),
    "T3 group_difference: line C thickness": lambda fs: has(
        fs, "group_difference", segment=("line", "C"), metric="thickness_mm"
    ),
    "T4 group_difference: night shift defects": lambda fs: has(
        fs, "group_difference", segment=("shift", "night"), metric="defects"
    ),
    "T5 skewness: defects": lambda fs: has(fs, "skewness", column="defects"),
    "T6 missing_pattern: pressure on line B": lambda fs: has(
        fs, "missing_pattern", segment=("line", "B"), column="pressure"
    ),
    "T7 trend_regression: temperature rises": lambda fs: has(
        fs, "trend_regression", column="temperature", direction="up"
    ),
    "T8 negative control: pressure and thickness": lambda fs: (
        not any(
            f.type == "correlation" and {f.facts["a"], f.facts["b"]} == {"pressure", "thickness_mm"}
            for f in fs
        )
    ),
}


def save(name: str, table: dict[str, bool]) -> None:
    REPORT_PATH.parent.mkdir(exist_ok=True)
    data = json.loads(REPORT_PATH.read_text()) if REPORT_PATH.exists() else {}
    data[name] = table
    REPORT_PATH.write_text(json.dumps(data, indent=2), encoding="utf-8")


def evaluate(name: str, patterns: dict[str, Check], findings: list[Finding]) -> dict[str, bool]:
    table = {label: check(findings) for label, check in patterns.items()}
    print(f"\n{name}: planted patterns")
    for label, ok in table.items():
        print(f"  {'yes' if ok else 'NO ':3}  {label}")
    save(name, table)
    return table


# ---------------------------------------------------------------- planted patterns


def test_sales_patterns_are_found(sales: DemoDataset) -> None:
    findings = report_for(sales, "sales").findings
    table = evaluate("sales", SALES_PATTERNS, findings)
    assert sum(table.values()) >= 7, table
    assert all(table.values()), [k for k, v in table.items() if not v]


def test_statistics_patterns_are_found_and_the_negative_control_is_not(
    statistics: DemoDataset,
) -> None:
    findings = report_for(statistics, "statistics").findings
    table = evaluate("statistics", STATISTICS_PATTERNS, findings)
    found = sum(v for k, v in table.items() if not k.startswith("T8"))
    assert found >= 6, table
    assert all(table.values()), [k for k, v in table.items() if not v]
    assert table["T8 negative control: pressure and thickness"]


def test_the_sales_detectors_report_no_failures(sales: DemoDataset) -> None:
    notes = report_for(sales, "sales").notes
    assert all("could not be completed" not in n for n in notes), notes


def test_the_statistics_detectors_report_no_failures(statistics: DemoDataset) -> None:
    notes = report_for(statistics, "statistics").notes
    assert all("could not be completed" not in n for n in notes), notes


def test_results_do_not_change_between_runs(statistics: DemoDataset) -> None:
    first = [f.id for f in report_for(statistics, "statistics").findings]
    second = [f.id for f in report_for(statistics, "statistics").findings]
    assert first == second


def test_every_finding_has_support_and_scores_in_range(
    sales: DemoDataset, statistics: DemoDataset
) -> None:
    for demo, pack in ((sales, "sales"), (statistics, "statistics")):
        for f in report_for(demo, pack).findings:
            assert f.support > 0, f.id
            assert 0 <= f.severity <= 1 and 0 <= f.confidence <= 1, f.id


def test_a_trend_on_unfinished_months_is_not_reported_as_a_collapse(sales: DemoDataset) -> None:
    """Won revenue by creation month would show -90%: the newest deals are still open."""
    findings = report_for(sales, "sales").findings
    revenue = [f for f in findings if f.type == "trend" and f.metric_id == "won_revenue"]
    assert all(abs(float(f.facts.get("change_pct", 0))) < 60 for f in revenue)
    assert not any(f.metric_id == "pipeline_value" and f.type == "trend" for f in findings)


# ---------------------------------------------------------------- noise


def test_noise_only_sales_data_gives_at_most_one_finding() -> None:
    demo = model_from_frame(noise_sales())
    findings = run_insights(demo.con, demo.model, Settings(_env_file=None), "sales").findings
    assert len(findings) <= 1, [f.title for f in findings]


def test_noise_only_measurements_give_at_most_one_finding() -> None:
    demo = model_from_frame(noise_measurements())
    findings = run_insights(demo.con, demo.model, Settings(_env_file=None), "statistics").findings
    assert len(findings) <= 1, [f.title for f in findings]


# ---------------------------------------------------------------- the statistics helpers


def test_benjamini_hochberg_matches_a_known_example() -> None:
    adjusted = bh_adjust([0.01, 0.04, 0.03, 0.005])
    assert adjusted == pytest.approx([0.02, 0.04, 0.04, 0.02])


def test_benjamini_hochberg_never_lowers_a_p_value_and_keeps_order() -> None:
    p = [0.2, 0.001, 0.5, 0.03, 0.03]
    adjusted = bh_adjust(p)
    assert all(a >= b for a, b in zip(adjusted, p, strict=True))
    assert all(0 <= a <= 1 for a in adjusted)
    assert bh_adjust([]) == []


def stats_frame(**columns: object) -> DemoDataset:
    rng = np.random.default_rng(5)
    rows = 600
    days = pd.Timestamp("2025-01-01") + pd.to_timedelta(np.sort(rng.integers(0, 365, rows)), "D")
    data = {
        "sample_id": [f"S{i:04d}" for i in range(rows)],
        "measured_at": days.strftime("%Y-%m-%d"),
    }
    data["line"] = rng.choice(["A", "B"], rows)
    data.update({k: (v(rng, rows) if callable(v) else v) for k, v in columns.items()})
    return model_from_frame(pd.DataFrame(data))


def types_found(demo: DemoDataset) -> set[str]:
    findings = run_insights(demo.con, demo.model, Settings(_env_file=None), "statistics").findings
    return {f.type for f in findings}


def test_left_skewed_columns_are_not_reported_as_a_few_very_large_values() -> None:
    demo = stats_frame(score=lambda rng, n: 100 - rng.exponential(5, n))
    assert "skewness" not in types_found(demo)


def test_right_skewed_columns_are_reported() -> None:
    demo = stats_frame(wait=lambda rng, n: rng.exponential(5, n))
    assert "skewness" in types_found(demo)


def test_a_normal_column_has_no_outliers_under_the_3_iqr_rule() -> None:
    demo = stats_frame(x=lambda rng, n: rng.normal(0, 1, n))
    assert "outliers" not in types_found(demo)


def test_a_few_extreme_values_are_reported_with_their_count() -> None:
    def with_spikes(rng: np.random.Generator, n: int) -> np.ndarray:
        values = rng.normal(10, 1, n)
        values[:6] = 60
        return values

    demo = stats_frame(x=with_spikes)
    findings = run_insights(demo.con, demo.model, Settings(_env_file=None), "statistics").findings
    found = [f for f in findings if f.type == "outliers"]
    assert found and found[0].facts["count"] == 6 and found[0].facts["extreme"] == 60


def test_a_perfect_copy_of_a_column_is_a_correlation_with_the_right_sign() -> None:
    rng = np.random.default_rng(1)
    base = rng.normal(0, 1, 600)
    inverse = stats_frame(a=base, b=-base * 2 + rng.normal(0, 0.1, 600))
    findings = run_insights(
        inverse.con, inverse.model, Settings(_env_file=None), "statistics"
    ).findings
    pairs = [f for f in findings if f.type == "correlation"]
    assert pairs and pairs[0].facts["direction"] == "down" and float(pairs[0].facts["rho"]) < -0.9


def test_a_tiny_difference_between_groups_is_not_reported_even_with_many_rows() -> None:
    rng = np.random.default_rng(3)
    rows = 20_000
    frame = pd.DataFrame(
        {
            "sample_id": [f"S{i}" for i in range(rows)],
            "line": rng.choice(["A", "B"], rows),
            "value": rng.normal(100, 10, rows),
        }
    )
    frame.loc[frame["line"] == "B", "value"] += 0.6  # significant, but d = 0.06
    demo = model_from_frame(frame)
    findings = run_insights(demo.con, demo.model, Settings(_env_file=None), "statistics").findings
    assert not [f for f in findings if f.type == "group_difference"]


def test_a_dataset_with_too_few_rows_is_skipped_quietly() -> None:
    small = model_from_frame(pd.DataFrame({"a": ["1", "2"], "b": ["3", "4"]}))
    report = run_insights(small.con, small.model, Settings(_env_file=None), "statistics")
    assert [f for f in report.findings if f.type in {"correlation", "outliers"}] == []


# ---------------------------------------------------------------- the sales detectors


def sales_variant(mutate: Callable[[pd.DataFrame], pd.DataFrame]) -> DemoDataset:
    return model_from_frame(mutate(noise_sales(8000)))


def sales_types(demo: DemoDataset) -> set[str]:
    return {
        f.type
        for f in run_insights(demo.con, demo.model, Settings(_env_file=None), "sales").findings
    }


def test_a_dominant_owner_gap_is_found_in_a_small_variant() -> None:
    def weaken(frame: pd.DataFrame) -> pd.DataFrame:
        weak = (frame["owner_id"] == "O03") & (frame["status"] == "won")
        flip = np.random.default_rng(9).random(len(frame)) < 0.6
        frame.loc[weak & flip, "status"] = "lost"
        frame.loc[weak & flip, "stage"] = "Proposal"
        return frame

    assert "win_rate_gap" in sales_types(sales_variant(weaken))


def test_a_funnel_with_a_broken_step_is_found() -> None:
    def break_step(frame: pd.DataFrame) -> pd.DataFrame:
        lost = (frame["status"] == "lost") & (frame["stage"] == "Negotiation")
        frame.loc[lost & (np.random.default_rng(2).random(len(frame)) < 0.8), "stage"] = "Proposal"
        return frame

    assert "funnel_drop" in sales_types(sales_variant(break_step))


def test_a_funnel_with_too_few_stages_is_skipped() -> None:
    def two_stages(frame: pd.DataFrame) -> pd.DataFrame:
        frame["stage"] = np.where(frame["status"] == "won", "Won", "Lead")
        return frame

    assert "funnel_drop" not in sales_types(sales_variant(two_stages))


def test_stalled_needs_a_real_share_of_old_open_value() -> None:
    def age(frame: pd.DataFrame) -> pd.DataFrame:
        old = frame["created_date"] < "2026-05-01"
        pick = old & (np.random.default_rng(4).random(len(frame)) < 0.25)
        frame.loc[pick, ["status", "close_date"]] = ["open", ""]
        frame.loc[pick, "stage"] = "Qualified"
        return frame

    assert "stalled" in sales_types(sales_variant(age))
    assert "stalled" not in sales_types(model_from_frame(noise_sales(8000)))
