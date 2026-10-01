"""Golden questions: every expected SQL runs on the flat view and returns rows.

The first test builds the flat view with plain pandas joins (independent of the ingestion code),
so it checks the demo data and the SQL. The second runs the same SQL on the real flat view.
"""

from pathlib import Path

import duckdb
import pytest
import yaml

from scripts.generate_demo_data import (
    generate_bank,
    generate_retail,
    generate_sales,
    generate_statistics,
)
from tests.helpers import DEMO_DIR, bank_flat, retail_flat


def load_questions() -> list[dict]:
    """Return all golden question entries."""
    with open(DEMO_DIR / "golden_questions.yaml", encoding="utf-8") as handle:
        return yaml.safe_load(handle)["questions"]


@pytest.fixture(scope="module")
def connections() -> dict[str, duckdb.DuckDBPyConnection]:
    """One in-memory DuckDB connection per dataset with a view named ``data``."""
    result = {}
    for name, frame in (("retail", retail_flat()), ("bank", bank_flat())):
        con = duckdb.connect(":memory:")
        con.register("flat_frame", frame)
        con.execute("CREATE VIEW data AS SELECT * FROM flat_frame")
        result[name] = con
    return result


def test_there_are_forty_questions() -> None:
    questions = load_questions()
    assert len(questions) == 40
    assert sum(q["dataset"] == "retail" for q in questions) == 20
    assert sum(q["dataset"] == "bank" for q in questions) == 20
    assert len({q["id"] for q in questions}) == 40


@pytest.mark.parametrize("question", load_questions(), ids=lambda q: q["id"])
def test_expected_sql_returns_rows(question: dict, connections: dict) -> None:
    rows = connections[question["dataset"]].execute(question["expected_sql"]).fetchall()
    assert len(rows) >= 1


@pytest.mark.parametrize("question", load_questions(), ids=lambda q: q["id"])
def test_expected_sql_runs_on_real_flat_view(
    question: dict, request: pytest.FixtureRequest
) -> None:
    """Same check as above, but on the view built by the real ingestion code (P2)."""
    dataset = request.getfixturevalue(question["dataset"])
    rows = dataset.con.execute(question["expected_sql"]).fetchall()
    assert len(rows) >= 1


def file_hashes(folder: Path) -> dict[str, bytes]:
    """Return the bytes of every CSV file below ``folder``."""
    return {str(p.relative_to(folder)): p.read_bytes() for p in sorted(folder.rglob("*.csv"))}


def test_generator_is_deterministic_and_small(tmp_path: Path) -> None:
    first, second = tmp_path / "a", tmp_path / "b"
    for target in (first, second):
        generate_retail(target)
        generate_bank(target)
        generate_sales(target)
        generate_statistics(target)
    assert file_hashes(first) == file_hashes(second)
    assert file_hashes(first) == file_hashes(DEMO_DIR)
    total_bytes = sum(len(content) for content in file_hashes(first).values())
    assert total_bytes < 25 * 1024 * 1024
