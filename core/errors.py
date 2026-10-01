"""Application error hierarchy.

The UI shows ``UserFacingError.message`` as is. Any other exception is shown as a generic
message with a short reference id, and the traceback is logged under that id.
"""

from __future__ import annotations

import logging
import traceback
import uuid

logger = logging.getLogger("finalyzer.errors")


class UserFacingError(Exception):
    """An error whose message is safe and useful to show to the user."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class ValidationError(UserFacingError):
    """Input (SQL, filter value, file content, expression) failed validation."""


class PrivacyBlockedError(UserFacingError):
    """The Egress Gate refused to send a payload."""


class LLMUnavailableError(UserFacingError):
    """The LLM could not be reached or returned unusable output."""


class QueryTimeoutError(UserFacingError):
    """A DuckDB statement ran longer than the configured timeout."""


def new_reference_id() -> str:
    """Return an 8-character id used to match a UI message with a log entry."""
    return uuid.uuid4().hex[:8]


def describe_exception(exc: BaseException) -> str:
    """Return the text to show for any exception.

    Known errors show their own message. Unknown ones are logged with a reference id.
    """
    if isinstance(exc, UserFacingError):
        return exc.message
    ref = new_reference_id()
    # Frames and the exception type only: the message of a database error can quote a data value.
    trace = "".join(traceback.format_tb(exc.__traceback__))
    logger.error("unhandled_exception ref=%s type=%s\n%s", ref, type(exc).__name__, trace)
    return f"Something went wrong (ref: {ref})"
