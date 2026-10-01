"""Single-line JSON logging.

Log records carry event names and counters only. Never pass data values, payloads or keys
to the logger.
"""

from __future__ import annotations

import json
import logging
import sys
from datetime import UTC, datetime

_CONFIGURED = False


class JsonLineFormatter(logging.Formatter):
    """Format each record as one JSON object on one line."""

    def format(self, record: logging.LogRecord) -> str:
        entry = {
            "ts": datetime.fromtimestamp(record.created, tz=UTC).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        if record.exc_info:
            # Keep the log on one line; newlines are escaped by json.dumps.
            entry["exc"] = self.formatException(record.exc_info)
        return json.dumps(entry, ensure_ascii=True)


def setup_logging(level: str = "INFO") -> None:
    """Configure the root logger once. Later calls only update the level."""
    global _CONFIGURED
    root = logging.getLogger()
    root.setLevel(level.upper())
    if _CONFIGURED:
        return
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(JsonLineFormatter())
    root.handlers = [handler]
    _CONFIGURED = True
