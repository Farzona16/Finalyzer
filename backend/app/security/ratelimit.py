"""In-memory sliding-window rate limiter (S7). One worker, so memory is the right place."""

from __future__ import annotations

import math
import threading
from collections import defaultdict, deque
from collections.abc import Callable
from dataclasses import dataclass

from app import clock
from app.config import BackendSettings
from app.errors import AppError

MAX_KEYS = 10_000


@dataclass(frozen=True)
class Policy:
    """At most ``limit`` hits per ``window_s`` seconds for one key."""

    name: str
    limit: int
    window_s: int


def policies_from(settings: BackendSettings) -> dict[str, Policy]:
    """The four policies of the spec, with limits taken from the settings."""
    return {
        "login": Policy("login", settings.rate_limit_login_per_min, 60),
        "api": Policy("api", settings.rate_limit_api_per_min, 60),
        "upload": Policy("upload", settings.rate_limit_upload_per_hour, 3600),
        "ask": Policy("ask", settings.rate_limit_ask_per_min, 60),
    }


class RateLimiter:
    """Counts hits per (policy, key) and tells how long to wait when a limit is hit."""

    def __init__(
        self, policies: dict[str, Policy], timer: Callable[[], float] = clock.monotonic
    ) -> None:
        self.policies = policies
        self._timer = timer
        self._hits: dict[tuple[str, str], deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def check(self, policy_name: str, key: str) -> None:
        """Record a hit; raise a 429 ``AppError`` with ``Retry-After`` when over the limit."""
        policy = self.policies[policy_name]
        now = self._timer()
        with self._lock:
            if len(self._hits) > MAX_KEYS:
                self._purge(now)
            hits = self._hits[(policy_name, key)]
            while hits and now - hits[0] >= policy.window_s:
                hits.popleft()
            if len(hits) >= policy.limit:
                wait = max(1, math.ceil(policy.window_s - (now - hits[0])))
                raise AppError("rate_limited", headers={"Retry-After": str(wait)})
            hits.append(now)

    def _purge(self, now: float) -> None:
        """Drop keys whose newest hit is outside its window (keeps memory bounded)."""
        for (name, key), hits in list(self._hits.items()):
            if not hits or now - hits[-1] >= self.policies[name].window_s:
                del self._hits[(name, key)]

    def reset(self) -> None:
        """Forget everything (used by tests)."""
        with self._lock:
            self._hits.clear()
