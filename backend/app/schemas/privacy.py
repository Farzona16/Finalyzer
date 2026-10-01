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


class PrivacyEventsOut(BaseModel):
    """GET /privacy/events."""

    events: list[AuditEvent] = Field(default_factory=list)
