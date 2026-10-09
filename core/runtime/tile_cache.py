"""LRU cache and query dedupe for dashboard tiles (WP7)."""

from __future__ import annotations

import hashlib
import json
import threading
from collections import OrderedDict
from typing import Any

from core.runtime.profile import load_limits
from core.runtime.watchdog import register_cache_clear

_LOCK = threading.Lock()
_CACHE: OrderedDict[str, Any] = OrderedDict()
_BYTES = 0


def _budget() -> int:
    return load_limits().tile_cache_mb * 1024 * 1024


def cache_key(dataset_id: str, version: str, tile_id: str, filters: dict[str, Any]) -> str:
    blob = json.dumps(
        {"d": dataset_id, "v": version, "t": tile_id, "f": filters},
        sort_keys=True, default=str, separators=(",", ":"),
    )
    return hashlib.sha256(blob.encode()).hexdigest()


def get(key: str) -> Any | None:
    with _LOCK:
        if key not in _CACHE:
            return None
        _CACHE.move_to_end(key)
        return _CACHE[key]


def put(key: str, value: Any) -> None:
    encoded = json.dumps(value, default=str).encode()
    size = len(encoded)
    with _LOCK:
        if key in _CACHE:
            _CACHE.move_to_end(key)
            _CACHE[key] = value
            return
        global _BYTES  # noqa: PLW0603
        budget = _budget()
        while _CACHE and (_BYTES + size > budget):
            _, old = _CACHE.popitem(last=False)
            _BYTES -= len(json.dumps(old, default=str).encode())
        _CACHE[key] = value
        _BYTES += size


def clear() -> None:
    with _LOCK:
        _CACHE.clear()
        global _BYTES  # noqa: PLW0603
        _BYTES = 0


register_cache_clear(clear)
