"""Free-profile ingest: sampling, corrupt isolation, blocked formats (phase V3)."""

from __future__ import annotations

import pytest

from core.config import Settings
from core.errors import UserFacingError
from core.ingest.loader import load_file, load_files
from core.ingest.relationships import fan_out_ok
from core.runtime.profile import clear_limits, load_limits
from core.runtime.sampling import sample_badge


def setup_function() -> None:
    clear_limits()


def teardown_function() -> None:
    clear_limits()


def test_ods_and_zip_are_refused_in_the_free_profile(monkeypatch) -> None:
    monkeypatch.setenv("RESOURCE_PROFILE", "free")
    clear_limits()
    with pytest.raises(UserFacingError, match="ods"):
        load_file("sheet.ods", b"PK\x03\x04", Settings(_env_file=None, max_file_mb=10))
    with pytest.raises(UserFacingError, match="zip"):
        load_file("pack.zip", b"PK\x03\x04", Settings(_env_file=None, max_file_mb=10))


def test_one_corrupt_file_does_not_stop_the_batch() -> None:
    notes: list[str] = []
    tables = load_files(
        [
            ("bad.csv", b"\x00\x00\x00binary"),
            ("good.csv", b"a,b\n1,2\n3,4\n"),
        ],
        Settings(_env_file=None),
        warnings=notes,
    )
    assert "good" in tables
    assert any("bad.csv" in note for note in notes)


def test_sample_badge_copy() -> None:
    assert sample_badge(0.4) == "Based on a sample (40%)"
    assert load_limits("free").max_files == 10


def test_fan_out_guard_is_exact(retail) -> None:
    con = retail.con
    assert fan_out_ok(con, "sales", "store_id", "stores", "store_id")
