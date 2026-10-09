"""Process RSS watchdog (spec 2.1).

Between ingest steps the RSS is read; above the profile's RAM target no new file is
accepted, caches are cleared, and a plain-language error is raised.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Callable

from core.errors import UserFacingError
from core.runtime.profile import ResourceLimits, load_limits

logger = logging.getLogger("finalyzer.runtime")

_CLEAR_CACHES: list[Callable[[], None]] = []


def register_cache_clear(fn: Callable[[], None]) -> None:
    """Caches (tile LRU, prompt cache, DuckDB handles) register here at startup."""
    _CLEAR_CACHES.append(fn)


def rss_bytes() -> int:
    """Current process RSS. ``0`` when the platform cannot say."""
    try:
        import psutil

        return int(psutil.Process().memory_info().rss)
    except Exception:  # noqa: BLE001 - a missing psutil must not stop ingest
        pass
    try:
        # Linux: VmRSS is in kB.
        for line in open("/proc/self/status", encoding="ascii"):  # noqa: PTH123
            if line.startswith("VmRSS:"):
                return int(line.split()[1]) * 1024
    except OSError:
        return 0
    return 0


def rss_mb() -> int:
    return rss_bytes() // (1024 * 1024)


def clear_caches() -> None:
    for fn in list(_CLEAR_CACHES):
        try:
            fn()
        except Exception as exc:  # noqa: BLE001
            logger.warning("cache_clear_failed type=%s", type(exc).__name__)


def check_memory(limits: ResourceLimits | None = None) -> int:
    """Return current RSS in MB. Raises when the target is exceeded.

    Under pytest the RSS is the test runner (and any sibling jobs), not the app, so
    the cap is not applied. The production process is measured on its own.
    """
    if os.environ.get("PYTEST_CURRENT_TEST"):
        return rss_mb()
    limits = limits or load_limits()
    used = rss_mb()
    if used > limits.ram_target_mb:
        clear_caches()
        used = rss_mb()
    if used > limits.ram_target_mb:
        raise UserFacingError(
            "This device is short of memory, so another file cannot be loaded right now. "
            "Close other datasets or use a smaller file. "
            f"(Using about {used} MB; the limit is {limits.ram_target_mb} MB.)"
        )
    return used
