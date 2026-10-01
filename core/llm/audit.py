"""In-memory audit log of every attempted external call. Cleared when the session ends."""

from __future__ import annotations

import threading

from core.models import AuditEvent


class AuditLog:
    """Thread-safe list of ``AuditEvent`` objects for one session."""

    def __init__(self) -> None:
        self._events: list[AuditEvent] = []
        self._lock = threading.Lock()

    def add(self, event: AuditEvent) -> AuditEvent:
        """Store an event and return it (the caller may update it later)."""
        with self._lock:
            self._events.append(event)
        return event

    @property
    def events(self) -> list[AuditEvent]:
        """A copy of the event list, oldest first."""
        with self._lock:
            return list(self._events)

    @property
    def total_bytes_sent(self) -> int:
        """Bytes that left the private network (blocked calls send nothing)."""
        return sum(e.payload_bytes for e in self.events if e.external and not e.blocked)

    @property
    def blocked_count(self) -> int:
        """Number of calls the Egress Gate refused."""
        return sum(1 for e in self.events if e.blocked)

    @property
    def tokens_used(self) -> int:
        """Tokens spent by calls that were sent."""
        return sum(e.tokens_in + e.tokens_out for e in self.events if not e.blocked)

    def export_jsonl(self) -> str:
        """One JSON object per line, for the download button."""
        lines = [event.model_dump_json() for event in self.events]
        return "\n".join(lines) + ("\n" if lines else "")

    def clear(self) -> None:
        """Delete all events (Clear session)."""
        with self._lock:
            self._events.clear()
