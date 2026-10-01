"""Structured JSON logs with a request id (S16).

Only event names, ids and counters are logged. Exceptions are logged as type and stack
frames, never with their message, because a database error can quote a data value.
"""

from __future__ import annotations

import json
import logging
import sys
import traceback
from contextvars import ContextVar
from datetime import UTC, datetime

request_id_var: ContextVar[str] = ContextVar("request_id", default="-")


class JsonFormatter(logging.Formatter):
    """One JSON object per line, including the current request id."""

    def format(self, record: logging.LogRecord) -> str:
        entry = {
            "ts": datetime.fromtimestamp(record.created, tz=UTC).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "logger": record.name,
            "request_id": request_id_var.get(),
            "msg": record.getMessage(),
        }
        if record.exc_info and record.exc_info[0] is not None:
            entry["exc_type"] = record.exc_info[0].__name__
            entry["frames"] = "".join(traceback.format_tb(record.exc_info[2]))
        return json.dumps(entry, ensure_ascii=True)


def setup_logging(level: str = "INFO") -> None:
    """Install the JSON handler on the root logger (idempotent)."""
    root = logging.getLogger()
    root.setLevel(level.upper())
    for handler in root.handlers:
        if isinstance(handler.formatter, JsonFormatter):
            return
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(JsonFormatter())
    root.addHandler(handler)
    # Uvicorn's own access log would print query strings; the app logs its own access line.
    logging.getLogger("uvicorn.access").disabled = True
    # HTTP client libraries log full URLs (with query strings) at INFO level.
    for noisy in ("httpx", "httpcore"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
