"""Privacy page: bytes sent outside and the exact payloads of this process."""

from __future__ import annotations

from pydantic import BaseModel, Field

from core.models import AuditEvent


class PrivacySummaryOut(BaseModel):
    """GET /privacy/summary."""

    bytes_sent: int
    blocked_calls: int
    tokens_used: int
    event_count: int
    mode: str
    privacy_level: str = "strict"
    template_mode: bool = True
    kill_switch: bool = False
    # Per scope: {"used", "limit", "remaining"} (session, daily). Tokens only, no prices.
    budgets: dict[str, dict[str, int]] = Field(default_factory=dict)
    # Per chain link: name, model, state (ok, circuit_open, credits_exhausted, ...).
    providers: list[dict[str, object]] = Field(default_factory=list)
    ephemeral: bool = True


class PrivacyEventsOut(BaseModel):
    """GET /privacy/events."""

    events: list[AuditEvent] = Field(default_factory=list)
