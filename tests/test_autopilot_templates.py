"""Dashboard templates, domain packs and the Uzbek text that ships with them."""

from __future__ import annotations

import json
import math
import re
from pathlib import Path
from typing import Any

import pytest

from core.autopilot.heuristics import label_templates
from core.metrics.registry import list_pack_names, load_pack
from core.models import ColumnInfo
from core.templates.resolver import (
    GRID_COLUMNS,
    TEMPLATE_DIR,
    auto_dimension,
    layout,
    list_templates,
    load_template,
    normalized_entropy,
    resolve_template,
)
from tests.helpers import DemoDataset

CHART_TYPES = {
    "kpi",
    "kpi_row",
    "big_number",
    "donut",
    "line",
    "area",
    "bar_h",
    "bar_v",
    "stacked_bar",
    "gauge",
    "bullet",
    "funnel",
    "heatmap",
    "treemap",
    "scatter",
    "histogram",
    "boxplot",
    "waterfall",
    "table",
}
APPENDIX_A = {
    "retail": {"line", "donut", "bar_h", "stacked_bar", "gauge", "bullet", "heatmap", "table"},
    "bank": {"line", "donut", "bar_v", "gauge", "stacked_bar", "heatmap", "table"},
    "sales": {
        "funnel",
        "big_number",
        "gauge",
        "line",
        "bar_h",
        "donut",
        "bullet",
        "histogram",
        "table",
    },
    "statistics": {"histogram", "boxplot", "heatmap", "scatter", "bar_v", "line", "table"},
    "generic": {"line", "donut", "bar_h", "table"},
    "project": {"donut", "line", "bullet", "gauge", "bar_h", "bar_v", "stacked_bar"},
}
WRONG_UZBEK_APOSTROPHE = re.compile("[oOgG][\u2019\u2018`\u00b4\u02bc']")
ROOT = Path(__file__).resolve().parents[1]


def all_pack_names() -> list[str]:
    return list_pack_names()


def uzbek_strings() -> list[tuple[str, str]]:
    """Every Uzbek string of the packs, label templates and dashboard templates."""
    found: list[tuple[str, str]] = []
    for name in all_pack_names():
        pack = load_pack(name)
        found.append((f"{name}.labels", pack.labels["uz"]))
        found += [(f"{name}.question", q) for q in pack.suggested_questions["uz"]]
        for metric in pack.metrics:
            for key, value in metric.i18n["uz"].items():
                values = value if isinstance(value, list) else [value]
                found += [(f"{name}.{metric.id}.{key}", v) for v in values]
    found += [(f"label.{k}", v["uz"]) for k, v in label_templates().items()]
    for name in list_templates():
        found += [(f"{name}.{t['id']}", t["title"]["uz"]) for t in load_template(name)["tiles"]]
    return found


# ------------------------------------------------------------------ packs


def test_every_domain_has_a_pack_and_a_template() -> None:
    assert set(all_pack_names()) >= {
        "generic",
        "retail",
        "bank",
        "sales",
        "statistics",
        "project",
        "expenses",
    }
    for name in all_pack_names():
        assert load_pack(name).dashboard_template in list_templates(), name


@pytest.mark.parametrize("name", all_pack_names())
def test_pack_fields_follow_the_spec(name: str) -> None:
    pack = load_pack(name)
    assert set(pack.labels) == {"uz", "en"}
    assert set(pack.suggested_questions) == {"uz", "en"}
    assert len(pack.suggested_questions["uz"]) >= 3
    assert pack.primary_metric in {m.id for m in pack.metrics}
    assert pack.detectors
    ids = [m.id for m in pack.metrics]
    assert len(ids) == len(set(ids))
    for metric in pack.metrics:
        uz = metric.i18n["uz"]
        assert {"label", "label_gen", "short", "description_plain", "synonyms"} <= set(uz)
        assert 1 <= metric.priority <= 100
        assert metric.source == "pack"


def test_sales_and_demo_packs_exist_for_the_new_domains() -> None:
    sales = {m.id for m in load_pack("sales").metrics}
    assert {
        "pipeline_value",
        "won_revenue",
        "deals_won",
        "deals_lost",
        "win_rate_pct",
        "avg_deal_size",
        "avg_cycle_days",
        "new_deals",
        "stalled_deals",
        "quota_attainment_pct",
    } == sales
    project = {m.id for m in load_pack("project").metrics}
    assert {
        "total_projects",
        "completed",
        "in_progress",
        "on_hold",
        "total_budget",
        "actual_cost",
        "cost_variance",
        "tasks_completed_pct",
        "deadline_met_pct",
    } == project


def test_uzbek_text_uses_the_right_letters() -> None:
    texts = uzbek_strings()
    assert len(texts) > 150
    for where, text in texts:
        assert text.strip(), where
        assert not WRONG_UZBEK_APOSTROPHE.search(text), (where, text)
        assert "\u02bc" not in text, (where, text)
    assert any("\u02bb" in text for _, text in texts)  # the real o and g letters are used


# ------------------------------------------------------------------ templates


@pytest.mark.parametrize("name", list_templates())
def test_template_files_are_well_formed(name: str) -> None:
    template = load_template(name)
    assert template["id"] == name and template["grid_columns"] == GRID_COLUMNS
    ids = [t["id"] for t in template["tiles"]]
    keys = [t["title_key"] for t in template["tiles"]]
    assert len(ids) == len(set(ids)) and len(keys) == len(set(keys))
    for tile in template["tiles"]:
        assert tile["type"] in CHART_TYPES
        assert 1 <= tile["size"]["w"] <= GRID_COLUMNS and tile["size"]["h"] >= 1
        assert tile["title"]["en"] and tile["title"]["uz"]
        assert isinstance(tile["cross_filter"], bool)
    types = {t["type"] for t in template["tiles"]}
    assert APPENDIX_A[name] <= types


def test_template_list_matches_the_files() -> None:
    assert set(list_templates()) == {p.stem for p in TEMPLATE_DIR.glob("*.json")}
    assert set(list_templates()) == set(APPENDIX_A)


def resolve(dataset: DemoDataset, pack: str, metric_ids: list[str] | None = None) -> list[dict]:
    from core.autopilot.run import run_autopilot
    from core.config import Settings

    result = run_autopilot(dataset.model, dataset.con, Settings(_env_file=None, llm_mode="offline"))
    ids = metric_ids if metric_ids is not None else [m.id for m in result.metrics]
    loaded = load_pack(pack)
    return resolve_template(
        loaded.dashboard_template,
        dataset.model,
        ids,
        loaded.primary_metric,
        result.tiles,
        dataset.con,
    )


def assert_grid_is_tight(tiles: list[dict[str, Any]]) -> None:
    taken: set[tuple[int, int]] = set()
    for tile in tiles:
        x, y = tile["position"]["x"], tile["position"]["y"]
        w, h = tile["size"]["w"], tile["size"]["h"]
        assert 0 <= x and x + w <= GRID_COLUMNS
        cells = {(x + i, y + j) for i in range(w) for j in range(h)}
        assert not cells & taken, tile["id"]
        taken |= cells
    rows = sorted({y for _, y in taken})
    assert rows == list(range(rows[-1] + 1)) if rows else True  # no empty row


@pytest.mark.parametrize(
    ("name", "minimum"), [("retail", 9), ("bank", 9), ("sales", 10), ("statistics", 7)]
)
def test_demo_dashboards_resolve_completely(
    name: str, minimum: int, request: pytest.FixtureRequest
) -> None:
    dataset: DemoDataset = request.getfixturevalue(name)
    tiles = resolve(dataset, name)
    assert len(tiles) >= minimum
    assert tiles[0]["type"] == "kpi_row" and tiles[0]["metric_ids"]
    assert_grid_is_tight(tiles)
    columns = {c.name for c in dataset.model.columns}
    for tile in tiles:
        dimension = tile["dimension"]
        if isinstance(dimension, str) and tile["type"] not in ("kpi_row",):
            known = (
                ":" in dimension
                or dimension in columns
                or dimension
                in (
                    "correlation",
                    "top_pair",
                    "describe",
                    "stalled_rows",
                    "weekday_by_week",
                    "auto",
                )
            )
            assert known, (tile["id"], dimension)


def test_unmet_tiles_are_dropped_and_the_grid_reflows(retail: DemoDataset) -> None:
    columns = [c for c in retail.model.columns if c.name != "channel"]
    model = retail.model.model_copy(update={"columns": columns})
    ids = ["revenue", "gross_margin_pct"]
    tiles = resolve_template("retail", model, ids, "revenue", ["revenue"], retail.con)
    names = [t["id"] for t in tiles]
    assert "revenue_by_channel" not in names and "revenue_trend" in names
    assert "orders_chart" not in names
    assert_grid_is_tight(tiles)
    assert "margin_gauge" in names
    assert "top_products" in names


def test_tiles_without_their_metric_are_dropped(bank: DemoDataset) -> None:
    tiles = resolve_template("bank", bank.model, ["txn_volume"], "txn_volume", [], bank.con)
    names = {t["id"] for t in tiles}
    assert "failed_gauge" not in names and "volume_trend" in names
    assert "kpi_row" not in names  # no KPI tiles, so no empty KPI row


def test_sales_bullet_depends_on_the_target_role(sales: DemoDataset) -> None:
    without = {t["id"] for t in resolve(sales, "sales")}
    assert "deal_size_by_owner" in without and "quota_by_owner" not in without
    model = sales.model.model_copy(update={"roles": {**sales.model.roles, "target": "amount"}})
    ids = ["quota_attainment_pct", "avg_deal_size", "new_deals", "win_rate_pct"]
    with_target = {
        t["id"] for t in resolve_template("sales", model, ids, "won_revenue", [], sales.con)
    }
    assert "quota_by_owner" in with_target and "deal_size_by_owner" not in with_target


def test_generic_template_needs_a_date_for_the_trend(generic_model: DemoDataset) -> None:
    tiles = resolve_template("generic", generic_model.model, ["row_count"], "row_count", [], None)
    assert "main_trend" not in {t["id"] for t in tiles}
    assert {"auto_share", "auto_top"} <= {t["id"] for t in tiles}


@pytest.fixture(scope="module")
def generic_model() -> DemoDataset:
    from tests.autopilot_fixtures import generic_contacts, model_from_frame

    return model_from_frame(generic_contacts())


def test_layout_wraps_rows_and_never_overlaps() -> None:
    tiles = [
        {"id": str(i), "size": {"w": w, "h": 2 + i % 2}}
        for i, w in enumerate((8, 4, 6, 6, 3, 5, 12))
    ]
    placed = layout(tiles)
    assert_grid_is_tight(placed)
    assert [(t["position"]["x"], t["position"]["y"]) for t in placed][:3] == [
        (0, 0),
        (8, 0),
        (0, 3),
    ]


# ------------------------------------------------------------------ the "auto" dimension


def test_entropy_is_one_for_even_counts_and_low_for_skewed_ones() -> None:
    assert normalized_entropy([10, 10, 10, 10]) == pytest.approx(1.0)
    assert normalized_entropy([97, 1, 1, 1]) < 0.2
    assert normalized_entropy([5]) == 0.0 and normalized_entropy([]) == 0.0


def test_auto_dimension_picks_the_most_even_column(sales: DemoDataset) -> None:
    chosen = auto_dimension(sales.model, sales.con)
    frame = sales.con.execute("SELECT * FROM data").df()
    best, best_entropy = None, -1.0
    for column in sales.model.columns:
        if column.dtype != "text" or not 3 <= column.distinct <= 12 or column.name == "owner_id":
            continue
        entropy = normalized_entropy(list(frame[column.name].value_counts().values))
        if entropy > best_entropy:
            best, best_entropy = column.name, entropy
    assert chosen == best


def test_auto_dimension_skips_personal_data_and_identifiers(sales: DemoDataset) -> None:
    only_bad = [
        ColumnInfo(name="email", dtype="text", is_pii=True, distinct=5, categories=list("abcde")),
        ColumnInfo(name="owner_id", dtype="text", distinct=8, categories=list("abcdefgh")),
        ColumnInfo(name="flag", dtype="text", distinct=2, categories=["a", "b"]),
    ]
    model = sales.model.model_copy(update={"columns": only_bad})
    assert auto_dimension(model, sales.con) is None


def test_prefer_breaks_a_tie_between_equal_columns(sales: DemoDataset) -> None:
    columns = [
        ColumnInfo(name="alpha", dtype="text", distinct=4, categories=list("abcd")),
        ColumnInfo(name="beta", dtype="text", distinct=4, categories=list("abcd")),
    ]
    model = sales.model.model_copy(update={"columns": columns})
    assert auto_dimension(model, None) == "alpha"
    assert auto_dimension(model, None, ["beta"]) == "beta"
    assert not math.isnan(normalized_entropy([1, 2, 3]))


def test_template_json_is_valid_utf8_without_bom() -> None:
    for path in TEMPLATE_DIR.glob("*.json"):
        raw = path.read_bytes()
        assert not raw.startswith(b"\xef\xbb\xbf")
        json.loads(raw.decode("utf-8"))
    assert (ROOT / "core" / "autopilot" / "labels.json").read_bytes()[:3] != b"\xef\xbb\xbf"
