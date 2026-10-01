"""Ranking, action templates and the Next steps block."""

from __future__ import annotations

import pytest

from core.config import Settings
from core.insights.actions import ACTION_TEMPLATES, actions_for, fill
from core.insights.engine import run_detectors
from core.insights.nextsteps import build_next_steps
from core.insights.ranking import (
    TOP_COUNT,
    confidence,
    confidence_level,
    priority_level,
    rank_findings,
    score,
    split_top,
)
from core.metrics.registry import available_metrics, primary_metric, unavailable_metrics
from core.models import Finding
from tests.helpers import DemoDataset
from tests.test_detectors_boring import build_boring


def finding(fid: str, severity: float, conf: float, type_: str = "trend") -> Finding:
    """A minimal finding for ranking tests."""
    return Finding(
        id=fid, type=type_, metric_id="revenue", title=fid, facts={"assessment": "bad"},
        support=100, severity=severity, confidence=conf,
    )


def test_confidence_formula() -> None:
    assert confidence(12, 100) == 1.0
    assert confidence(6, 50) == 0.25
    assert confidence(24, 1000) == 1.0
    assert confidence(3, None) == 0.25
    assert confidence(None, 25) == 0.25
    assert confidence() == 1.0


def test_ranking_orders_by_severity_times_confidence() -> None:
    a, b, c = finding("a", 1.0, 0.2), finding("b", 0.5, 0.9), finding("c", 0.6, 0.5)
    assert [f.id for f in rank_findings([a, b, c])] == ["b", "c", "a"]
    assert score(b) == pytest.approx(0.45)


def test_ranking_ties_keep_detector_order() -> None:
    items = [finding(name, 0.5, 0.5) for name in "xyz"]
    assert [f.id for f in rank_findings(items)] == ["x", "y", "z"]


def test_split_top_shows_five_and_keeps_the_rest() -> None:
    items = [finding(f"f{i}", 1 - i / 20, 1.0) for i in range(8)]
    top, rest = split_top(items)
    assert len(top) == TOP_COUNT == 5
    assert [f.id for f in top] == [f"f{i}" for i in range(5)]
    assert len(rest) == 3
    assert split_top([]) == ([], [])


def test_levels_follow_thresholds() -> None:
    assert confidence_level(finding("a", 1, 0.9)) == "High"
    assert confidence_level(finding("a", 1, 0.5)) == "Medium"
    assert confidence_level(finding("a", 1, 0.1)) == "Low"
    assert priority_level(finding("a", 1, 0.9)) == "High"
    assert priority_level(finding("a", 0.5, 0.6)) == "Medium"
    assert priority_level(finding("a", 0.2, 0.5)) == "Low"


def test_every_finding_type_has_one_or_two_actions(retail_findings) -> None:
    for item in retail_findings:
        values = {
            "metric": "Revenue", "period": "2026-09", "segment": "X", "share": "50",
            "date": "2026-08-12", "dimension": "category", "high": "A", "low": "B",
            "column": "discount",
        }
        actions = actions_for(item, values)
        assert 1 <= len(actions) <= 2, item.title
        assert not any("{" in a for a in actions)


def test_missing_placeholder_drops_the_template_and_falls_back() -> None:
    assert fill("Look at {segment}", {}) is None
    assert fill("Look at {segment}", {"segment": "X"}) == "Look at X"
    assert actions_for(finding("a", 1, 1, "unknown_type"), {}) == [
        "Review this finding with the data owner."
    ]


def test_action_templates_add_no_numbers_of_their_own() -> None:
    for texts in ACTION_TEMPLATES.values():
        for text in texts:
            stripped = text.replace("{share}%", "")
            assert not any(ch.isdigit() for ch in stripped), text


def test_next_steps_on_retail_demo(retail: DemoDataset, retail_findings) -> None:
    metrics = available_metrics(retail.model)
    primary = primary_metric(retail.model)
    settings = Settings(_env_file=None)
    report = run_detectors(retail.con, retail.model, metrics, primary, settings)
    unavailable = unavailable_metrics(retail.model)
    steps = build_next_steps(retail.model, metrics, primary, unavailable, report)
    assert 1 <= len(steps.track) <= 3
    assert steps.track[0].startswith(primary.label)
    assert any("discount" in line for line in steps.quality)
    assert any("Personal data" in line for line in steps.quality)
    assert steps.missing == []


def test_next_steps_name_missing_roles_and_how_to_unlock() -> None:
    demo = build_boring()
    metrics = available_metrics(demo.model)
    primary = primary_metric(demo.model)
    report = run_detectors(demo.con, demo.model, metrics, primary, Settings(_env_file=None))
    unavailable = unavailable_metrics(demo.model)
    steps = build_next_steps(demo.model, metrics, primary, unavailable, report)
    cost = [line for line in steps.missing if line.startswith("No cost column")]
    assert cost and "Add one to unlock" in cost[0]
    assert "gross profit and margin could not be computed" in cost[0]
    assert steps.quality == []
