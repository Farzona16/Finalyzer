"""Identifier masking: bidirectional rewrite, literals, collisions, and the live NL2SQL path."""

from __future__ import annotations

import re

from core.models import ColumnInfo, DatasetModel
from core.query.anonymizer import IdentifierMask
from core.query.prompts import build_messages, build_user_payload
from tests.fakes import clarify_reply, make_settings, query_reply
from tests.test_nl2sql_voting import make_agent


def _has_ident(text: str, name: str) -> bool:
    return re.search(rf"(?<![\w]){re.escape(name)}(?![\w])", text, flags=re.I) is not None


def _model(names: list[str], fact: str = "sales") -> DatasetModel:
    return DatasetModel(
        dataset_id="t",
        fact_table=fact,
        tables={fact: 1},
        columns=[ColumnInfo(name=name, dtype="text") for name in names],
        row_count=1,
        version="1",
    )


def test_from_model_uses_tbl_a_and_numbered_columns() -> None:
    mask = IdentifierMask.from_model(_model(["city", "txn_count"]))
    assert mask.table == "tbl_a"
    assert mask.forward["data"] == "tbl_a"
    assert mask.forward["city"] == "col_1"
    assert mask.forward["txn_count"] == "col_2"
    assert mask.reverse["tbl_a"] == "data"
    assert mask.reverse["col_1"] == "city"


def test_roundtrip_sql_skips_string_literals() -> None:
    mask = IdentifierMask.from_model(_model(["city", "txn_count"]))
    sql = "SELECT city, COUNT(*) AS n FROM data WHERE city = 'city' GROUP BY city"
    masked = mask.mask_text(sql)
    assert masked == "SELECT col_1, COUNT(*) AS n FROM tbl_a WHERE col_1 = 'city' GROUP BY col_1"
    assert mask.unmask_sql(masked) == sql


def test_quoted_identifiers_and_longer_tokens() -> None:
    mask = IdentifierMask.from_model(_model(["city", "col_1"]))
    assert mask.forward["city"] != "col_1"
    assert mask.forward["col_1"].startswith("col_")
    sql = 'SELECT "city", col_10 FROM data'
    masked = mask.mask_text(sql)
    assert '"city"' not in masked
    assert "col_10" in masked  # not a mapped token; left alone
    assert mask.unmask_sql(masked) == sql
    assert mask.unmask_sql('SELECT "col_10" FROM tbl_a') == 'SELECT "col_10" FROM data'


def test_date_trunc_and_sum_are_not_rewritten() -> None:
    mask = IdentifierMask.from_model(_model(["sale_date", "amount"]))
    sql = "SELECT date_trunc('month', sale_date) AS month, SUM(amount) FROM data"
    masked = mask.mask_text(sql)
    assert "date_trunc" in masked and "SUM" in masked
    assert "sale_date" not in masked and "amount" not in masked
    assert mask.unmask_sql(masked) == sql


def test_cyrillic_column_roundtrip() -> None:
    mask = IdentifierMask.from_model(_model(["сумма"]))
    assert mask.unmask_sql(mask.mask_text('SELECT "сумма" FROM data')) == 'SELECT "сумма" FROM data'


def test_collision_skips_tokens_that_are_real_names() -> None:
    mask = IdentifierMask.from_model(_model(["col_1", "tbl_a"]))
    assert mask.forward["data"] != "tbl_a"
    assert mask.forward["col_1"] != "col_1"
    assert "tbl_a" not in mask.forward.values() or mask.forward.get("tbl_a") == "tbl_a"


def test_build_messages_masks_system_and_user_payload(retail) -> None:
    from core.metrics.registry import available_metrics

    settings = make_settings()
    mask = IdentifierMask.from_model(retail.model)
    payload = build_user_payload(
        retail.model, available_metrics(retail.model), "Total amount by city", settings, None, ""
    )
    messages = build_messages(payload, mask)
    sent = "\n".join(item["content"] for item in messages)
    assert "tbl_a" in sent
    for name in ("amount", "sale_date", "store_name", "city", "data"):
        assert not _has_ident(sent, name)
    assert "Electronics" not in sent
    assert "values omitted" in sent


def test_live_agent_hides_identifiers_and_restores_sql(retail) -> None:
    mask = IdentifierMask.from_model(retail.model)
    sql = f"SELECT {mask.forward['city']}, COUNT(*) AS n FROM {mask.table} GROUP BY 1"
    agent, provider, _ = make_agent(retail, [query_reply(sql)], sql_candidates=1)
    answer = agent.ask("How many rows per city?")
    assert answer.status == "ok"
    assert answer.result is not None
    assert "city" in answer.result.columns
    assert all(not column.startswith("col_") for column in answer.result.columns)
    assert "tbl_a" not in answer.text and "col_" not in answer.text
    assert "FROM data" in answer.result.sql
    assert "city" in answer.result.sql
    sent = "\n".join(item["content"] for item in provider.calls[0])
    for name in (column.name for column in retail.model.columns):
        assert not _has_ident(sent, name)
    assert "`data`" not in sent


def test_clarify_text_is_unmasked_for_the_ui(retail) -> None:
    mask = IdentifierMask.from_model(retail.model)
    token = mask.forward["city"]
    agent, _, _ = make_agent(retail, [clarify_reply(f"Did you mean {token}?")], sql_candidates=1)
    answer = agent.ask("How did we do?")
    assert answer.status == "clarify"
    assert answer.text == "Did you mean city?"
    assert token not in answer.text


def test_category_values_are_mapped_and_restored() -> None:
    model = _model(["city"])
    model.columns[0] = model.columns[0].model_copy(update={"categories": ["Op 15", "Op 02"]})
    mask = IdentifierMask.from_model(model)
    assert mask.value_forward["Op 15"].startswith("val_")
    text = "How many rows for Op 15 in city?"
    masked = mask.mask_text(text)
    assert "Op 15" not in masked
    assert mask.unmask_text(masked) == text
    token = mask.value_forward["Op 15"]
    sql = f"SELECT COUNT(*) FROM tbl_a WHERE {mask.forward['city']} = '{token}'"
    assert "Op 15" in mask.unmask_sql(sql)


def test_unmask_spec_restores_nested_column_names() -> None:
    mask = IdentifierMask.from_model(_model(["city", "txn_count"]))
    raw = {
        "agg": "ratio",
        "columns": [mask.forward["txn_count"]],
        "filter": [{"column": mask.forward["city"], "op": "=", "value": "Tashkent"}],
        "numerator": {"agg": "sum", "columns": mask.forward["txn_count"]},
    }
    restored = mask.unmask_spec(raw)
    assert restored["columns"] == ["txn_count"]
    assert restored["filter"][0]["column"] == "city"
    assert restored["filter"][0]["value"] == "Tashkent"
    assert restored["numerator"]["columns"] == "txn_count"


def test_repair_error_is_remasked_before_the_model(retail) -> None:
    mask = IdentifierMask.from_model(retail.model)
    error = "Table 'nope' is not available. Use only 'data'."
    remasked = mask.mask_error(error)
    assert "data" not in remasked
    assert mask.table in remasked
    quoted = 'Referenced column "amount" not found in FROM clause'
    assert mask.forward["amount"] in mask.mask_error(quoted)
    assert "amount" not in mask.mask_error(quoted)

    def reply(messages: list[dict[str, str]], temperature: float) -> str:
        last = messages[-1]["content"]
        if "failed with this error" in last:
            assert "Use only 'data'" not in last
            assert f"Use only '{mask.table}'" in last
            return query_reply("SELECT COUNT(*) AS n FROM tbl_a")
        return query_reply("SELECT COUNT(*) AS n FROM nope")

    agent, _, _ = make_agent(retail, [reply], sql_candidates=1)
    answer = agent.ask("How many sales do we have?")
    assert answer.status == "ok"
    assert answer.result is not None and answer.result.columns == ["n"]
