"""Request and response models for authentication and the current user."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict

from app.schemas.common import (
    Currency,
    Email,
    Language,
    PersonName,
    PrivacyMode,
    ReadingLevel,
    SecretText,
    Theme,
)


class StrictModel(BaseModel):
    """Rejects unknown fields so typos and mass-assignment attempts fail loudly."""

    model_config = ConfigDict(extra="forbid")


class CsrfOut(BaseModel):
    """The CSRF token to send back in the ``X-CSRF-Token`` header."""

    csrf_token: str


class LoginIn(StrictModel):
    """Sign-in form."""

    email: Email
    password: SecretText


class FirstAdminIn(StrictModel):
    """First-run form: creates the workspace and its administrator."""

    email: Email
    password: SecretText
    display_name: PersonName
    workspace_name: PersonName


class ChangePasswordIn(StrictModel):
    """Change the own password."""

    current_password: SecretText
    new_password: SecretText


class PreferencesIn(StrictModel):
    """Own display preferences; every field is optional."""

    language: Language | None = None
    theme: Theme | None = None
    reading_level: ReadingLevel | None = None


class UserOut(BaseModel):
    """A user as shown to clients (never includes the hash)."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    email: str
    display_name: str
    role: str
    is_active: bool
    language: str
    theme: str
    reading_level: str
    created_at: datetime
    last_login_at: datetime | None


class WorkspaceOut(BaseModel):
    """Workspace policy visible to every member."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    privacy_mode: PrivacyMode
    currency: Currency
    language: Language


class MeOut(BaseModel):
    """The signed-in user, the workspace and what the user may do."""

    user: UserOut
    workspace: WorkspaceOut
    permissions: list[str]


class VersionOut(BaseModel):
    """Public build information; ``first_run_required`` drives the first-run screen."""

    version: str
    first_run_required: bool
