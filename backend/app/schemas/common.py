"""Small shared field types."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import StringConstraints

EMAIL_PATTERN = r"^[^@\s]+@[^@\s]+\.[^@\s]+$"

Email = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True, to_lower=True, min_length=5, max_length=254, pattern=EMAIL_PATTERN
    ),
]
# Login passwords are only length-limited; the strength policy applies when one is created.
SecretText = Annotated[str, StringConstraints(min_length=1, max_length=256)]
PersonName = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=120)]

Language = Literal["uz", "en"]
Theme = Literal["light", "dark", "system"]
ReadingLevel = Literal["simple", "expert"]
RoleName = Literal["admin", "analyst", "viewer"]
Currency = Literal["UZS", "USD", "EUR", "RUB"]
PrivacyMode = Literal["demo", "cloud", "local"]
