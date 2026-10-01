"""Request and response models for the admin area."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel

from app.schemas.auth import StrictModel
from app.schemas.common import (
    Currency,
    Email,
    Language,
    PersonName,
    PrivacyMode,
    RoleName,
    SecretText,
)


class UserCreateIn(StrictModel):
    """Admin creates a user with an initial password."""

    email: Email
    display_name: PersonName
    role: RoleName
    password: SecretText


class UserPatchIn(StrictModel):
    """Admin edits a user; only the given fields change."""

    display_name: PersonName | None = None
    role: RoleName | None = None
    is_active: bool | None = None
    new_password: SecretText | None = None


class PolicyIn(StrictModel):
    """Workspace policy edited by an admin."""

    privacy_mode: PrivacyMode
    currency: Currency
    language: Language
    retention_days: int | None = None


class PolicyOut(BaseModel):
    """Workspace policy plus the modes the server allows."""

    privacy_mode: PrivacyMode
    allowed_privacy_modes: list[PrivacyMode]
    currency: Currency
    language: Language
    retention_days: int | None


class AuditItemOut(BaseModel):
    """One audit record."""

    seq: int
    ts: datetime
    event: str
    actor_user_id: str | None
    detail: dict[str, object]
    request_id: str
    prev_hash: str
    hash: str


class AuditPageOut(BaseModel):
    """A page of audit records, newest first."""

    total: int
    items: list[AuditItemOut]


class AuditVerifyOut(BaseModel):
    """Result of recomputing the chain."""

    ok: bool
    checked: int
    broken_at_seq: int | None
    reason: str
