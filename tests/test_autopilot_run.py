"""KPI Autopilot end to end: domain detection, offline metrics, AI suggestions, validation."""

from __future__ import annotations

import json
import math
from typing import Any

import pandas as pd
import pytest

from core.autopilot import validate as validate_module
from core.autopilot.classifier import classify
from core.autopilot.dsl import Context, compile_sql, parse_spec
from core.autopilot.run import AutopilotResult, run_autopilot
from core.autopilot.suggest import finalize_labels, make_tiebreak
from core.autopilot.validate import load_frame, reference_value, spec_columns, validate_spec
from core.config import Settings
from core.llm.gateway import LLMGateway
from core.models import ColumnInfo
from core.query.anonymizer import IdentifierMask
from tests.autopilot_fixtures import CLASSIFIER_FIXTURES, hr_attrition, model_from_frame
from tests.fakes import FakeProvider, make_settings
from tests.helpers import DemoDataset, bank_flat, retail_flat, sales_flat, statistics_flat

OFFLINE = Settings(_env_file=None, llm_mode="offline")
MAX_OFFLINE_SECONDS = 5.0
DEMOS = ("retail", "bank", "sales", "statistics")
EXPECTED_PACK = {"retail": "retail", "bank": "bank", "sales": "sales", "statistics": "statistics"}


@pytest.fixture(scope="module")
def results(request: pytest.FixtureRequest) -> dict[str, AutopilotResult]:
    """The offline autopilot result of every demo dataset (computed once)."""
    out = {}
    for name in DEMOS:
        dataset: DemoDataset = request.getfixturevalue(name)
        out[name] = run_autopilot(dataset.model, dataset.con, OFFLINE)
    return out


def live_gateway(reply: Any, **settings: Any) -> tuple[LLMGateway, FakeProvider]:
    provider = FakeProvider([reply if isinstance(reply, str) else json.dumps(reply)])
    return LLMGateway(make_settings(**settings), provider=provider), provider


# ------------------------------------------------------------------ domain detection


def test_classifier_gets_at_least_11_of_12_fixture_files_right() -> None:
    wrong = []
    for fixture in CLASSIFIER_FIXTURES:
        pack = classify(model_from_frame(fixture.build()).model).pack
        if pack != fixture.expected:
            wrong.append((fixture.name, fixture.expected, pack))
    assert len(CLASSIFIER_FIXTURES) == 12
    assert len(wrong) <= 1, wrong


@pytest.mark.parametrize("name", DEMOS)
def test_demo_datasets_are_recognised(name: str, results: dict[str, AutopilotResult]) -> None:
    classification = results[name].classification
    assert classification.pack == EXPECTED_PACK[name]
    assert classification.confidence >= 0.6
    assert classification.evidence
    assert classification.to_dict()["level"] in ("high", "medium")


def test_user_override_wins(sales: DemoDataset) -> None:
    result = run_autopilot(sales.model, sales.con, OFFLINE, pack_override="generic")
    assert result.classification.pack == "generic"
    assert result.classification.source == "user"


def test_tiebreak_asks_the_model_only_when_one_exists(sales: DemoDataset) -> None:
    assert make_tiebreak(None, sales.model) is None
    gateway, provider = live_gateway({"pack": "bank"})
    tiebreak = make_tiebreak(gateway, sales.model)
    assert tiebreak is not None
    assert tiebreak(["sales", "bank"]) == "bank"
    assert "Enterprise" not in provider.calls[0][1]["content"]


# ------------------------------------------------------------------ offline behaviour


@pytest.mark.parametrize("name", DEMOS)
def test_offline_autopilot_is_fast(name: str, results: dict[str, AutopilotResult]) -> None:
    assert results[name].seconds <= MAX_OFFLINE_SECONDS


@pytest.mark.parametrize("name", DEMOS)
def test_tiles_are_sensible(name: str, results: dict[str, AutopilotResult]) -> None:
    result = results[name]
    ids = [m.id for m in result.metrics]
    assert len(ids) == len(set(ids))
    assert 6 <= len(result.tiles) <= 8
    assert set(result.tiles) <= set(ids)
    primary = {"retail": "revenue", "bank": "txn_volume", "sales": "won_revenue"}.get(name)
    if primary:
        assert result.tiles[0] == primary
    assert all(math.isfinite(v) for v in result.values.values())


def test_no_key_means_no_model_calls(sales: DemoDataset) -> None:
    settings = Settings(_env_file=None, llm_mode="live", llm_api_key="")
    gateway = LLMGateway(settings)
    assert gateway.is_offline
    result = run_autopilot(sales.model, sales.con, settings, gateway)
    assert result.metrics and not any(m.source == "ai" for m in result.metrics)


def test_pack_metrics_are_kept_and_repeats_are_dropped(
    results: dict[str, AutopilotResult],
) -> None:
    sales_result = results["sales"]
    sources = {m.id: m.source for m in sales_result.metrics}
    assert sources["win_rate_pct"] == "pack"
    assert sources["m_share_status_lost"] == "heuristic"
    reasons = dict(sales_result.dropped)
    assert reasons["m_rows"] == "same value as another metric"  # new_deals is COUNT(*)


def test_every_metric_has_uzbek_text(results: dict[str, AutopilotResult]) -> None:
    for result in results.values():
        for metric in result.metrics:
            assert metric.i18n["uz"]["label"].strip(), metric.id


@pytest.mark.parametrize("name", DEMOS)
def test_spec_metrics_match_pandas_on_independent_data(
    name: str, results: dict[str, AutopilotResult], request: pytest.FixtureRequest
) -> None:
    """The same specs, evaluated on frames read straight from the CSV files, equal the SQL."""
    dataset: DemoDataset = request.getfixturevalue(name)
    frame = {
        "retail": retail_flat,
        "bank": bank_flat,
        "sales": sales_flat,
        "statistics": statistics_flat,
    }[name]()
    if name == "statistics":
        frame["passed"] = frame["passed"] == "yes"
    ctx = Context.from_model(dataset.model)
    checked = 0
    for metric in results[name].metrics:
        if metric.spec is None or not spec_columns(parse_spec(metric.spec, ctx)) <= set(frame):
            continue
        spec = parse_spec(metric.spec, ctx)
        expected = reference_value(spec, frame, ctx)
        assert expected is not None
        assert math.isclose(results[name].values[metric.id], expected, rel_tol=1e-6), metric.id
        checked += 1
    assert checked >= 8


# ------------------------------------------------------------------ the unseen domain


def test_unseen_hr_data_gets_valid_metrics_without_any_input() -> None:
    dataset = model_from_frame(hr_attrition())
    result = run_autopilot(dataset.model, dataset.con, OFFLINE)
    ctx = Context.from_model(dataset.model)
    from_specs = [m for m in result.metrics if m.spec is not None]
    assert len(from_specs) >= 4
    frame = load_frame(
        dataset.con, {c for m in from_specs for c in spec_columns(parse_spec(m.spec, ctx))}
    )
    for metric in from_specs:
        outcome = validate_spec(parse_spec(metric.spec, ctx), ctx, dataset.con, frame, OFFLINE)
        assert outcome.ok, (metric.id, outcome.reason)
    assert not [d for d in result.dropped if "parity" in d[1] or "Error" in d[1]]
    labels = {m.label for m in from_specs}
    assert any("attrition" in label.lower() for label in labels)


# ------------------------------------------------------------------ validation


@pytest.fixture
def sales_ctx(sales: DemoDataset) -> tuple[Context, pd.DataFrame]:
    ctx = Context.from_model(sales.model)
    return ctx, load_frame(sales.con, set(ctx.columns))


def check(
    raw: dict[str, Any],
    sales: DemoDataset,
    sales_ctx: tuple[Context, pd.DataFrame],
    ctx: Context | None = None,
) -> validate_module.Validation:
    base, frame = sales_ctx
    use = ctx or base
    return validate_spec(parse_spec(raw, use), use, sales.con, frame, OFFLINE)


def test_a_good_metric_passes(sales: DemoDataset, sales_ctx: Any) -> None:
    outcome = check({"agg": "avg", "columns": ["amount"]}, sales, sales_ctx)
    assert outcome.ok and outcome.value and outcome.value > 0


def test_parity_mismatch_is_dropped(
    sales: DemoDataset, sales_ctx: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(validate_module, "reference_value", lambda *a: 1.0)
    outcome = check({"agg": "avg", "columns": ["amount"]}, sales, sales_ctx)
    assert not outcome.ok and "parity" in outcome.reason


def test_missing_reference_is_dropped(
    sales: DemoDataset, sales_ctx: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(validate_module, "reference_value", lambda *a: None)
    assert not check({"agg": "avg", "columns": ["amount"]}, sales, sales_ctx).ok


def test_constant_and_mostly_empty_columns_are_dropped(sales: DemoDataset, sales_ctx: Any) -> None:
    base, _ = sales_ctx

    def changed(**update: Any) -> Context:
        columns = dict(base.columns)
        columns["amount"] = columns["amount"].model_copy(update=update)
        return Context(columns, base.roles, base.row_count)

    raw = {"agg": "avg", "columns": ["amount"]}
    assert "constant" in check(raw, sales, sales_ctx, changed(distinct=1)).reason
    assert "empty" in check(raw, sales, sales_ctx, changed(null_pct=60.0)).reason


def test_a_filter_that_matches_nothing_is_dropped(sales: DemoDataset, sales_ctx: Any) -> None:
    base, _ = sales_ctx
    columns = dict(base.columns)
    info = columns["status"]
    columns["status"] = info.model_copy(update={"categories": [*info.categories, "ghost"]})
    ctx = Context(columns, base.roles, base.row_count)
    raw = {"agg": "count", "filter": [{"column": "status", "op": "=", "value": "ghost"}]}
    assert "no rows" in check(raw, sales, sales_ctx, ctx).reason


def test_a_negative_duration_is_dropped(sales: DemoDataset, sales_ctx: Any) -> None:
    raw = {"agg": "avg_days_between", "columns": ["close_date", "created_date"]}
    assert "before" in check(raw, sales, sales_ctx).reason
    forward = {"agg": "avg_days_between", "columns": ["created_date", "close_date"]}
    assert check(forward, sales, sales_ctx).ok


def test_sql_errors_become_dropped_metrics_not_exceptions(
    sales: DemoDataset, sales_ctx: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    def boom(*args: Any) -> None:
        raise RuntimeError("database exploded")

    monkeypatch.setattr(validate_module, "run_sql", boom)
    outcome = check({"agg": "avg", "columns": ["amount"]}, sales, sales_ctx)
    assert not outcome.ok and "RuntimeError" in outcome.reason


# ------------------------------------------------------------------ AI suggestions


AI_REPLY = {
    "metrics": [
        {
            "id": "top_region_share",
            "agg": "ratio",
            "times100": True,
            "unit": "percent",
            "numerator": {
                "agg": "count",
                "filter": [{"column": "region", "op": "=", "value": "Tashkent"}],
            },
            "denominator": {"agg": "count"},
            "label": {"en": "Win rate", "uz": "Moslashtirilgan nom"},
        },
        {
            "id": "p90_deal",
            "agg": "quantile",
            "q": 0.9,
            "columns": ["amount"],
            "label": {"en": "Large deal value", "uz": "Katta bitim qiymati"},
        },
        {
            "id": "referral_deal",
            "agg": "avg",
            "columns": ["amount"],
            "filter": [{"column": "source", "op": "=", "value": "referral"}],
            "label": {"en": "Average referral deal"},
        },
        {"id": "evil", "agg": "sum; DROP TABLE data", "columns": ["amount"]},
        {"id": "ghost", "agg": "avg", "columns": ["salary"]},
        {
            "id": "inject",
            "agg": "count",
            "filter": [{"column": "status", "op": "=", "value": "won' OR '1'='1"}],
        },
        {
            "id": "dup",
            "agg": "ratio",
            "times100": True,
            "numerator": {
                "agg": "count",
                "filter": [{"column": "status", "op": "=", "value": "lost"}],
            },
            "denominator": {"agg": "count"},
        },
    ]
}


def test_ai_metrics_are_checked_compiled_and_labelled(sales: DemoDataset) -> None:
    gateway, provider = live_gateway(AI_REPLY)
    result = run_autopilot(sales.model, sales.con, make_settings(), gateway)
    ai = {m.id: m for m in result.metrics if m.source == "ai"}
    assert set(ai) == {"ai_top_region_share", "ai_p90_deal", "ai_referral_deal"}
    assert len(provider.calls) == 1
    sent = "\n".join(item["content"] for item in provider.calls[0])
    assert "tbl_a" in sent and "`data`" not in sent
    reasons = dict(result.dropped)
    assert reasons["evil"].startswith("invalid") and reasons["ghost"].startswith("invalid")
    assert reasons["inject"].startswith("invalid") and reasons["dup"] == "duplicate"
    assert "DROP" not in " ".join(m.expr for m in result.metrics)
    # glossary wins over the model's wording; a model label is marked; a missing one falls back
    assert ai["ai_top_region_share"].i18n["uz"]["label"] == "Yutish ulushi"
    assert not ai["ai_top_region_share"].ai_translated
    assert ai["ai_p90_deal"].ai_translated
    assert ai["ai_referral_deal"].i18n["uz"]["label"] == "Average referral deal"
    assert ai["ai_referral_deal"].ai_translated


def test_garbage_from_the_model_changes_nothing(sales: DemoDataset, results: Any) -> None:
    baseline = {m.id for m in run_autopilot(sales.model, sales.con, OFFLINE).metrics}
    for reply in (
        "not json at all",
        json.dumps({"metrics": "nope"}),
        json.dumps({"metrics": [1, None, "x"]}),
    ):
        gateway, _ = live_gateway(reply)
        result = run_autopilot(sales.model, sales.con, make_settings(), gateway)
        assert {m.id for m in result.metrics} == baseline


def test_a_failing_provider_falls_back_to_offline_metrics(sales: DemoDataset) -> None:
    provider = FakeProvider([RuntimeError("down")])
    gateway = LLMGateway(make_settings(), provider=provider)
    result = run_autopilot(sales.model, sales.con, make_settings(), gateway)
    assert result.metrics and not any(m.source == "ai" for m in result.metrics)


def test_cloud_mode_keeps_category_values_and_personal_columns_out_of_the_prompt(
    sales: DemoDataset,
) -> None:
    columns = [
        *sales.model.columns,
        ColumnInfo(
            name="owner_email",
            dtype="text",
            is_pii=True,
            distinct=12,
            categories=["ann@example.com"],
        ),
    ]
    model = sales.model.model_copy(update={"columns": columns})
    gateway, provider = live_gateway({"metrics": []}, privacy_mode="cloud")
    run_autopilot(model, sales.con, make_settings(privacy_mode="cloud"), gateway)
    sent = "\n".join(item["content"] for item in provider.calls[0])
    mask = IdentifierMask.from_model(model)
    assert f"{mask.forward['amount']} (" in sent and f"{mask.forward['stage']} (" in sent
    assert "`data`" not in sent and "amount (" not in sent
    assert "Enterprise" not in sent and "website" not in sent
    assert "owner_email" not in sent and "ann@example.com" not in sent
    demo, demo_provider = live_gateway({"metrics": []})
    run_autopilot(model, sales.con, make_settings(), demo)
    assert "Enterprise" not in demo_provider.calls[0][1]["content"]


def test_label_rules() -> None:
    labels, machine = finalize_labels({"en": "Churn", "uz": "xx"})
    assert labels["uz"] == "Mijozlar ketishi" and not machine
    assert finalize_labels({"en": "Spread", "uz": "Tarqoqlik"}) == (
        {"en": "Spread", "uz": "Tarqoqlik"},
        True,
    )
    assert finalize_labels({"en": "Spread"})[0]["uz"] == "Spread"


def test_compiled_ai_sql_is_the_only_sql(sales: DemoDataset) -> None:
    ctx = Context.from_model(sales.model)
    spec = parse_spec(AI_REPLY["metrics"][1], ctx)
    gateway, _ = live_gateway(AI_REPLY)
    result = run_autopilot(sales.model, sales.con, make_settings(), gateway)
    ai = next(m for m in result.metrics if m.id == "ai_p90_deal")
    assert ai.expr == compile_sql(spec, ctx)
