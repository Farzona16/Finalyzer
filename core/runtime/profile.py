"""The one place that reads ``RESOURCE_PROFILE`` (spec 1).

Other modules use the numbers on :class:`ResourceLimits`, never the profile name.
If the env var is unset, a cgroup memory limit of 768 MB or less selects the free set.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

FREE_MEMORY_BYTES = 768 * 1024 * 1024
CGROUP_V2 = Path("/sys/fs/cgroup/memory.max")
CGROUP_V1 = Path("/sys/fs/cgroup/memory/memory.limit_in_bytes")


@dataclass(frozen=True)
class ResourceLimits:
    """Numbers every other module may read. There is no profile-name field on purpose."""

    ram_target_mb: int
    ingest_workers: int
    max_file_mb: int
    max_total_mb: int
    max_files: int
    max_rows: int
    duckdb_max_memory: str
    duckdb_threads: int
    duckdb_temp_directory: str
    ask_candidates: int
    insight_precompute: str
    analyst_findings: int
    analyst_rounds: int
    analyst_calls: int
    analyst_wall_s: int
    tile_query_parallelism: int
    tile_cache_mb: int
    sample_threshold_mb: int
    allow_ods: bool
    allow_zip: bool
    session_token_budget: int
    dataset_token_budget: int
    daily_token_budget: int


def cgroup_memory_bytes() -> int | None:
    """Container memory limit in bytes, or ``None`` when the host is unbounded."""
    for path in (CGROUP_V2, CGROUP_V1):
        try:
            text = path.read_text(encoding="ascii").strip()
        except OSError:
            continue
        if not text or text == "max":
            return None
        try:
            value = int(text)
        except ValueError:
            return None
        # Some hosts report a sentinel near 2^63 meaning "no limit".
        if value <= 0 or value >= 1 << 62:
            return None
        return value
    return None


def detect_profile(explicit: str | None = None, memory_bytes: int | None = None) -> str:
    """``free`` or ``full``. ``explicit`` wins; otherwise the cgroup size decides."""
    chosen = (explicit if explicit is not None else os.environ.get("RESOURCE_PROFILE", "")).strip().lower()
    if chosen in ("free", "full"):
        return chosen
    mem = memory_bytes if memory_bytes is not None else cgroup_memory_bytes()
    if mem is not None and mem <= FREE_MEMORY_BYTES:
        return "free"
    return "full"


def _cpu_count() -> int:
    return max(1, os.cpu_count() or 1)


def _temp_directory() -> str:
    if os.name == "nt":
        return str(Path(os.environ.get("TEMP", "C:/Temp")) / "finalyzer-duck")
    return "/tmp/duck"


def _full_limits() -> ResourceLimits:
    """Full-profile numbers. DuckDB may use half of the cgroup (or 2 GB) and every CPU."""
    mem = cgroup_memory_bytes()
    ram_mb = max(2048, (mem // (1024 * 1024)) if mem else 2048)
    duck_mb = max(256, ram_mb // 2)
    cpus = _cpu_count()
    return ResourceLimits(
        ram_target_mb=ram_mb,
        ingest_workers=4,
        max_file_mb=50,
        max_total_mb=300,
        max_files=30,
        max_rows=int(os.environ.get("MAX_ROWS", "1000000")),
        duckdb_max_memory=f"{duck_mb}MB",
        duckdb_threads=cpus,
        duckdb_temp_directory=_temp_directory(),
        ask_candidates=2,
        insight_precompute="after_build",
        analyst_findings=5,
        analyst_rounds=3,
        analyst_calls=12,
        analyst_wall_s=20,
        tile_query_parallelism=cpus,
        tile_cache_mb=int(os.environ.get("TILE_CACHE_MB", "256")),
        sample_threshold_mb=800,
        allow_ods=True,
        allow_zip=True,
        session_token_budget=int(os.environ.get("SESSION_TOKEN_BUDGET", "200000")),
        dataset_token_budget=int(os.environ.get("DATASET_TOKEN_BUDGET", "400000")),
        daily_token_budget=int(os.environ.get("DAILY_TOKEN_BUDGET", "1000000")),
    )


def _free_limits() -> ResourceLimits:
    return ResourceLimits(
        ram_target_mb=400,
        ingest_workers=1,
        max_file_mb=10,
        max_total_mb=30,
        max_files=10,
        max_rows=500_000,
        duckdb_max_memory="256MB",
        duckdb_threads=1,
        duckdb_temp_directory=_temp_directory(),
        ask_candidates=1,
        insight_precompute="on_demand",
        analyst_findings=3,
        analyst_rounds=2,
        analyst_calls=6,
        analyst_wall_s=45,
        tile_query_parallelism=2,
        tile_cache_mb=64,
        sample_threshold_mb=120,
        allow_ods=False,
        allow_zip=False,
        session_token_budget=30_000,
        dataset_token_budget=80_000,
        daily_token_budget=200_000,
    )


def load_limits(explicit: str | None = None, memory_bytes: int | None = None) -> ResourceLimits:
    """Cached limits for this process. Tests pass ``explicit`` and call :func:`clear_limits`."""
    return _limits_for(detect_profile(explicit, memory_bytes), memory_bytes)


@lru_cache(maxsize=8)
def _limits_for(name: str, memory_bytes: int | None) -> ResourceLimits:
    """Cache by the resolved profile name so a later env change is not sticky."""
    return _free_limits() if name == "free" else _full_limits()


def clear_limits() -> None:
    """Drop the process cache (tests)."""
    _limits_for.cache_clear()


def duckdb_pragmas(limits: ResourceLimits | None = None) -> list[str]:
    """Statements applied to every DuckDB connection."""
    limits = limits or load_limits()
    Path(limits.duckdb_temp_directory).mkdir(parents=True, exist_ok=True)
    return [
        f"SET memory_limit='{limits.duckdb_max_memory}'",
        f"SET max_memory='{limits.duckdb_max_memory}'",
        f"SET threads={limits.duckdb_threads}",
        f"SET temp_directory='{limits.duckdb_temp_directory.replace(chr(92), '/')}'",
        "SET preserve_insertion_order=false",
        "SET enable_external_access=false",
    ]
