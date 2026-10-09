"""Token budgets (spec 3.4): per session, per dataset and per UTC day.

The session total is the audit log's token count. Dataset and daily totals live here and are
shared by every gateway of the process (``LLMRuntime.ledger``).
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from datetime import UTC, datetime

from core.config import Settings


def utc_day() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%d")


class TokenLedger:
    """Thread-safe token counters by dataset and by day."""

    def __init__(self, today: Callable[[], str] = utc_day) -> None:
        self._today = today
        self._lock = threading.Lock()
        self._daily: dict[str, int] = {}
        self._datasets: dict[str, int] = {}

    def add(self, tokens: int, dataset_id: str | None) -> None:
        with self._lock:
            day = self._today()
            self._daily = {day: self._daily.get(day, 0) + tokens}
            if dataset_id:
                self._datasets[dataset_id] = self._datasets.get(dataset_id, 0) + tokens

    def used_today(self) -> int:
        with self._lock:
            return self._daily.get(self._today(), 0)

    def used_for(self, dataset_id: str | None) -> int:
        if not dataset_id:
            return 0
        with self._lock:
            return self._datasets.get(dataset_id, 0)

    def reset(self) -> None:
        with self._lock:
            self._daily.clear()
            self._datasets.clear()


def over_budget(
    settings: Settings,
    ledger: TokenLedger,
    session_used: int,
    estimate: int,
    dataset_id: str | None,
) -> str | None:
    """The first budget that ``estimate`` more tokens would exceed, or ``None``."""
    if session_used + estimate > settings.session_token_budget:
        return "session"
    if dataset_id and ledger.used_for(dataset_id) + estimate > settings.dataset_token_budget:
        return "dataset"
    if ledger.used_today() + estimate > settings.daily_token_budget:
        return "daily"
    return None


def budget_summary(settings: Settings, ledger: TokenLedger, session_used: int,
                   dataset_id: str | None = None) -> dict[str, dict[str, int]]:
    """Used and remaining tokens per scope (the Privacy page shows these; no prices)."""

    def scope(used: int, limit: int) -> dict[str, int]:
        return {"used": used, "limit": limit, "remaining": max(0, limit - used)}

    out = {
        "session": scope(session_used, settings.session_token_budget),
        "daily": scope(ledger.used_today(), settings.daily_token_budget),
    }
    if dataset_id:
        out["dataset"] = scope(ledger.used_for(dataset_id), settings.dataset_token_budget)
    return out
