"""Backend settings. Startup validation fails closed (S15): a bad config means no server."""

from __future__ import annotations

import base64
import binascii
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import ValidationError, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = BACKEND_ROOT.parent

PRIVACY_MODES = ("demo", "cloud", "local")
MIN_SECRET_LENGTH = 32
WEAK_SECRETS = {
    "changeme",
    "change-me",
    "secret",
    "password",
    "default",
    "development",
    "dev",
    "test",
    "finalyzer",
    "your-secret-key",
    "replace-me",
    "please-change-me",
}


class ConfigError(Exception):
    """The configuration is unusable. The message never contains a secret value."""


class BackendSettings(BaseSettings):
    """Everything the backend reads from the environment or a ``.env`` file."""

    model_config = SettingsConfigDict(
        env_file=(REPO_ROOT / ".env", BACKEND_ROOT / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
        validate_default=True,  # so a missing APP_SECRET_KEY fails instead of passing as ""
    )

    app_env: Literal["development", "test", "production"] = "development"
    app_secret_key: str = ""
    app_master_key: str = ""
    database_url: str = f"sqlite:///{(BACKEND_ROOT / 'finalyzer.db').as_posix()}"
    auto_migrate: bool = True

    storage_mode: Literal["ephemeral", "persistent"] = "ephemeral"
    data_dir: Path = REPO_ROOT / "data" / "runtime"
    session_idle_minutes: int = 30

    # Uploads (S9). Per-file and per-request byte limits; zip bombs are refused by ratio and size.
    max_upload_mb: int = 50
    max_request_mb: int = 200
    max_upload_files: int = 10
    max_rows: int = 1_000_000
    max_zip_expansion: int = 20
    max_uncompressed_mb: int = 500
    # DuckDB manager: how many datasets stay open, and the memory each connection may use.
    max_open_datasets: int = 3
    duckdb_memory_limit: str = "512MB"
    # Seconds between idle/retention sweeps (0 turns the background sweep off).
    purge_interval_seconds: int = 60
    job_workers: int = 2

    privacy_mode: Literal["demo", "cloud", "local"] = "demo"
    allowed_privacy_modes: str = "demo,cloud,local"

    access_token_minutes: int = 15
    refresh_token_days: int = 7
    cookie_secure: bool | None = None
    trust_proxy_headers: bool = False

    rate_limit_login_per_min: int = 5
    rate_limit_api_per_min: int = 120
    rate_limit_upload_per_hour: int = 10
    rate_limit_ask_per_min: int = 30

    frontend_dist: Path = REPO_ROOT / "frontend" / "dist"
    log_level: str = "INFO"

    @field_validator("app_secret_key")
    @classmethod
    def check_secret(cls, value: str) -> str:
        """The signing key must be long, not a known placeholder, and not one repeated letter."""
        if len(value) < MIN_SECRET_LENGTH:
            raise ValueError(f"APP_SECRET_KEY is missing or shorter than {MIN_SECRET_LENGTH}")
        if value.lower() in WEAK_SECRETS or len(set(value)) < 8:
            raise ValueError("APP_SECRET_KEY is too weak")
        return value

    @field_validator("app_master_key")
    @classmethod
    def check_master_key(cls, value: str) -> str:
        """The master key, when present, is 32 random bytes in base64."""
        if value and len(decode_master_key(value)) != 32:
            raise ValueError("APP_MASTER_KEY must be 32 bytes encoded as base64")
        return value

    @field_validator("database_url")
    @classmethod
    def normalize_database_url(cls, value: str) -> str:
        """Render hands out ``postgres://`` URLs; SQLAlchemy needs a driver name."""
        for old in ("postgres://", "postgresql://"):
            if value.startswith(old):
                return "postgresql+psycopg://" + value[len(old) :]
        return value

    @field_validator("allowed_privacy_modes")
    @classmethod
    def check_allowed_modes(cls, value: str) -> str:
        """A comma-separated, non-empty subset of the known privacy modes."""
        modes = [item.strip() for item in value.split(",") if item.strip()]
        if not modes or any(mode not in PRIVACY_MODES for mode in modes):
            raise ValueError("ALLOWED_PRIVACY_MODES must list demo, cloud and/or local")
        return ",".join(dict.fromkeys(modes))

    @model_validator(mode="after")
    def check_combinations(self) -> BackendSettings:
        """Rules that involve more than one setting."""
        if self.storage_mode == "persistent" and not self.app_master_key:
            raise ValueError("STORAGE_MODE=persistent requires APP_MASTER_KEY")
        if self.privacy_mode not in self.allowed_modes:
            raise ValueError("PRIVACY_MODE is not listed in ALLOWED_PRIVACY_MODES")
        if self.app_env == "production" and self.cookie_secure is False:
            raise ValueError("insecure cookies are refused in production")
        return self

    @property
    def allowed_modes(self) -> tuple[str, ...]:
        """The privacy modes a workspace admin may choose."""
        return tuple(self.allowed_privacy_modes.split(","))

    @property
    def secure_cookies(self) -> bool:
        """Cookies are ``Secure`` everywhere except local development and tests."""
        if self.cookie_secure is not None:
            return self.cookie_secure
        return self.app_env == "production"

    @property
    def is_production(self) -> bool:
        """True when running with ``APP_ENV=production``."""
        return self.app_env == "production"


def decode_master_key(value: str) -> bytes:
    """Decode a base64 key; raise ``ValueError`` (not a binascii error) when invalid."""
    try:
        return base64.b64decode(value, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ValueError("APP_MASTER_KEY is not valid base64") from exc


def load_settings() -> BackendSettings:
    """Build settings; turn validation problems into a ``ConfigError`` without values."""
    try:
        return BackendSettings()
    except ValidationError as exc:
        problems = "; ".join(
            f"{'.'.join(str(part) for part in err['loc']) or 'settings'}: "
            f"{str(err['msg']).removeprefix('Value error, ')}"
            for err in exc.errors()
        )
        raise ConfigError(f"Invalid configuration: {problems}") from exc


@lru_cache(maxsize=1)
def get_settings() -> BackendSettings:
    """The process-wide settings object."""
    return load_settings()
