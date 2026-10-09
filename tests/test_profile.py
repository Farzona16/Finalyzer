"""Resource profile numbers and DuckDB pragmas (phase V1b)."""

from __future__ import annotations

import pytest

from core.runtime.profile import (
    clear_limits,
    detect_profile,
    duckdb_pragmas,
    load_limits,
)
from core.runtime.sampling import estimated_bytes, maybe_sample_frame, sample_badge


def setup_function() -> None:
    clear_limits()


def teardown_function() -> None:
    clear_limits()


def test_explicit_free_and_full_do_not_depend_on_cgroup() -> None:
    free = load_limits("free")
    full = load_limits("full")
    assert free.ingest_workers == 1
    assert free.max_file_mb == 10
    assert free.max_total_mb == 30
    assert free.max_files == 10
    assert free.max_rows == 500_000
    assert free.duckdb_max_memory == "256MB"
    assert free.duckdb_threads == 1
    assert free.ask_candidates == 1
    assert free.insight_precompute == "on_demand"
    assert free.analyst_findings == 3
    assert free.tile_query_parallelism == 2
    assert free.tile_cache_mb == 64
    assert free.allow_ods is False
    assert free.allow_zip is False
    assert full.ingest_workers == 4
    assert full.max_file_mb == 50
    assert full.ask_candidates == 2
    assert full.insight_precompute == "after_build"
    assert full.allow_ods is True
    assert full.allow_zip is True


def test_cgroup_768mb_or_less_selects_free() -> None:
    assert detect_profile("", 512 * 1024 * 1024) == "free"
    assert detect_profile("", 768 * 1024 * 1024) == "free"
    assert detect_profile("", 769 * 1024 * 1024) == "full"
    assert detect_profile("full", 64 * 1024 * 1024) == "full"


def test_other_modules_see_numbers_not_a_profile_name() -> None:
    limits = load_limits("free")
    assert not hasattr(limits, "name")
    assert not hasattr(limits, "profile")


def test_duckdb_pragmas_include_the_spec_settings(tmp_path, monkeypatch) -> None:
    from core.runtime import profile as module

    monkeypatch.setattr(module, "_temp_directory", lambda: str(tmp_path / "duck"))
    clear_limits()
    statements = duckdb_pragmas(load_limits("free"))
    assert any("max_memory='256MB'" in s for s in statements)
    assert any(s == "SET threads=1" for s in statements)
    assert any("preserve_insertion_order=false" in s for s in statements)
    assert (tmp_path / "duck").is_dir()


def test_watchdog_stops_ingest_when_rss_is_over_the_target(monkeypatch) -> None:
    from core.errors import UserFacingError
    from core.runtime.watchdog import check_memory

    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    monkeypatch.setattr("core.runtime.watchdog.rss_mb", lambda: 500)
    monkeypatch.setattr("core.runtime.watchdog.clear_caches", lambda: None)
    with pytest.raises(UserFacingError, match="short of memory"):
        check_memory(load_limits("free"))
    monkeypatch.setattr("core.runtime.watchdog.rss_mb", lambda: 100)
    assert check_memory(load_limits("free")) == 100


def test_sample_badge_and_seeded_sample() -> None:
    import pandas as pd

    from core.runtime.profile import ResourceLimits, load_limits

    assert sample_badge(1.0) is None
    assert sample_badge(0.12) == "Based on a sample (12%)"
    assert "12%" in (sample_badge(0.12, "uz") or "")
    limits = load_limits("free")
    tight = ResourceLimits(**{**limits.__dict__, "sample_threshold_mb": 0})
    frame = pd.DataFrame({"a": list(range(100)), "b": list(range(100))})
    first, fraction = maybe_sample_frame(frame, tight)
    second, _ = maybe_sample_frame(frame, tight)
    assert 0 < fraction < 1
    assert list(first["a"]) == list(second["a"])
    assert estimated_bytes(10, 3) == 10 * 3 * 48
