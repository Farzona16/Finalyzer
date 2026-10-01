"""Application settings read from the environment and an optional ``.env`` file."""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import ValidationError as PydanticValidationError
from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from core.errors import UserFacingError


class Settings(BaseSettings):
    """All runtime configuration. Secrets come only from the environment."""

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    privacy_mode: Literal["demo", "cloud", "local"] = "demo"
    llm_mode: Literal["live", "offline"] = "live"
    llm_model: str = ""
    llm_api_key: str = ""
    llm_api_base: str = ""
    llm_timeout_s: int = 60
    llm_max_retries: int = 3
    sql_candidates: int = 3
    session_token_budget: int = 200_000
    send_category_values: bool = False
    max_file_mb: int = 50
    max_rows: int = 1_000_000
    query_timeout_s: int = 10
    result_row_limit: int = 10_000
    duckdb_memory_limit: str = "1GB"
    k_anon_min: int = 5
    app_password: str = ""
    log_level: str = "INFO"

    @model_validator(mode="after")
    def check_local_mode(self) -> Settings:
        """Local mode must point at a local model server, otherwise nothing can run."""
        if self.privacy_mode == "local" and not self.llm_api_base.strip():
            raise ValueError(
                "PRIVACY_MODE=local requires LLM_API_BASE (for example http://localhost:11434). "
                "Set it in the environment or in .env."
            )
        return self

    @property
    def effective_llm_mode(self) -> Literal["live", "offline"]:
        """Return ``offline`` when explicitly set or when no key is available.

        Local mode needs no key because the model runs on the user's machine.
        """
        if self.llm_mode == "offline":
            return "offline"
        if self.privacy_mode != "local" and not self.llm_api_key.strip():
            return "offline"
        if not self.llm_model.strip():
            return "offline"
        return "live"

    @property
    def category_values_allowed(self) -> bool:
        """Whether distinct category values may be sent to the LLM (decision D7)."""
        if self.privacy_mode == "cloud":
            return self.send_category_values
        return True

    @property
    def max_result_rows_to_llm(self) -> int:
        """Rows of query results the LLM may see (decision D8)."""
        return 0 if self.privacy_mode == "cloud" else 50


def load_settings() -> Settings:
    """Build settings and turn validation problems into a clear user-facing error."""
    try:
        return Settings()
    except PydanticValidationError as exc:
        problems = "; ".join(
            f"{'.'.join(str(p) for p in err['loc']) or 'settings'}: "
            f"{str(err['msg']).removeprefix('Value error, ')}"
            for err in exc.errors()
        )
        raise UserFacingError(f"Invalid configuration: {problems}") from exc


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the process-wide settings object."""
    return load_settings()
