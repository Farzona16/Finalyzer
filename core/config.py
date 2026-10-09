"""Application settings read from the environment and an optional ``.env`` file."""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import ValidationError as PydanticValidationError
from pydantic import field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from core.errors import UserFacingError

CLOUD_PROVIDERS = ("anthropic", "grok", "custom")
KNOWN_LINKS = (*CLOUD_PROVIDERS, "ollama", "offline")
CLEARANCES = ("synthetic_only", "strict_ok", "balanced_ok")


class Settings(BaseSettings):
    """All runtime configuration. Secrets come only from the environment.

    Model names are never hard-coded (D25): they come from the environment or the
    ``.env.free.example`` / ``.env.full.example`` presets.
    """

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    privacy_mode: Literal["demo", "cloud", "local"] = "demo"
    # cloud and live are the same; local = Ollama only; offline = frames and golden answers.
    llm_mode: Literal["live", "cloud", "local", "offline"] = "live"
    llm_provider: str = "anthropic"
    llm_provider_chain: str = "anthropic,grok,offline"

    anthropic_api_key: str = ""
    anthropic_base_url: str = "https://api.anthropic.com"
    claude_model_fast: str = ""
    claude_model_main: str = ""
    claude_model_deep: str = ""

    xai_api_key: str = ""
    openai_base_url: str = "https://api.x.ai/v1"
    grok_model_fast: str = ""
    grok_model_main: str = ""
    grok_model_deep: str = ""

    # Ollama (local mode) and the generic OpenAI-compatible "custom" link.
    llm_model: str = ""
    llm_api_key: str = ""
    llm_api_base: str = ""

    llm_timeout_s: int = 25
    llm_max_retries: int = 2
    circuit_failures: int = 3
    circuit_reset_s: int = 60
    sql_candidates: int = 1
    ask_candidates: int | None = None
    cache_identical_prompts: bool = True

    egress_allowed_hosts: str = "api.anthropic.com,api.x.ai"
    privacy_level: Literal["strict", "balanced", "open"] = "strict"
    provider_clearance: str = "anthropic:synthetic_only,grok:synthetic_only"
    kill_switch_default: Literal["on", "off"] = "off"

    session_token_budget: int = 30_000
    dataset_token_budget: int = 80_000
    daily_token_budget: int = 200_000
    hard_stop_on_budget: bool = True

    resource_profile: Literal["", "free", "full"] = ""
    insight_precompute: Literal["", "on_demand", "after_build"] = ""
    parity_sample: int = 0
    verify_pass: bool = False

    send_category_values: bool = False
    uz_polish: bool = False  # UZ_POLISH: optional AI rephrasing of Uzbek insight cards (off)
    max_file_mb: int = 50
    max_rows: int = 1_000_000
    query_timeout_s: int = 10
    result_row_limit: int = 10_000
    duckdb_memory_limit: str = "1GB"
    k_anon_min: int = 5
    app_password: str = ""
    log_level: str = "INFO"

    @field_validator("llm_provider_chain")
    @classmethod
    def check_chain(cls, value: str) -> str:
        """Known links only; ``offline`` is always the last link."""
        links = [item.strip().lower() for item in value.split(",") if item.strip()]
        unknown = [link for link in links if link not in KNOWN_LINKS]
        if unknown:
            raise ValueError(f"LLM_PROVIDER_CHAIN has unknown providers: {', '.join(unknown)}")
        links = [link for link in dict.fromkeys(links) if link != "offline"]
        return ",".join([*links, "offline"])

    @field_validator("provider_clearance")
    @classmethod
    def check_clearance(cls, value: str) -> str:
        """``provider:level`` pairs with a known level."""
        for pair in filter(None, (item.strip() for item in value.split(","))):
            name, _, level = pair.partition(":")
            if not name.strip() or level.strip() not in CLEARANCES:
                raise ValueError(
                    "PROVIDER_CLEARANCE entries must look like anthropic:synthetic_only "
                    "(levels: synthetic_only, strict_ok, balanced_ok)"
                )
        return value

    @model_validator(mode="after")
    def check_local_mode(self) -> Settings:
        """Local mode must point at a local model server, otherwise nothing can run."""
        local = self.privacy_mode == "local" or self.llm_mode == "local"
        if local and not self.llm_api_base.strip():
            raise ValueError(
                "PRIVACY_MODE=local requires LLM_API_BASE (for example http://localhost:11434). "
                "Set it in the environment or in .env."
            )
        return self

    @property
    def chain(self) -> list[str]:
        """Provider links in order, ``offline`` last."""
        if self.llm_mode == "offline":
            return ["offline"]
        if self.llm_mode == "local" or self.privacy_mode == "local":
            return ["ollama", "offline"]
        return self.llm_provider_chain.split(",")

    def provider_configured(self, name: str) -> bool:
        """Whether a link has what it needs (key and at least one model, or a local base)."""
        if name == "anthropic":
            return bool(self.anthropic_api_key.strip() and self.claude_models)
        if name == "grok":
            return bool(self.xai_api_key.strip() and self.grok_models)
        if name == "custom":
            return bool(
                self.llm_api_key.strip() and self.llm_model.strip() and self.llm_api_base.strip()
            )
        if name == "ollama":
            return bool(self.llm_api_base.strip() and self.llm_model.strip())
        return False

    @property
    def claude_models(self) -> dict[str, str]:
        return _tiers(self.claude_model_fast, self.claude_model_main, self.claude_model_deep)

    @property
    def grok_models(self) -> dict[str, str]:
        return _tiers(self.grok_model_fast, self.grok_model_main, self.grok_model_deep)

    @property
    def effective_llm_mode(self) -> Literal["live", "offline"]:
        """``offline`` when explicitly set or when no link in the chain is configured."""
        if self.llm_mode == "offline":
            return "offline"
        if any(self.provider_configured(link) for link in self.chain):
            return "live"
        return "offline"

    @property
    def clearance(self) -> dict[str, str]:
        """Provider name -> clearance level. Providers not listed are ``synthetic_only``."""
        out: dict[str, str] = {}
        for pair in filter(None, (item.strip() for item in self.provider_clearance.split(","))):
            name, _, level = pair.partition(":")
            out[name.strip().lower()] = level.strip()
        return out

    @property
    def allowed_hosts(self) -> frozenset[str]:
        return frozenset(
            host.strip().lower() for host in self.egress_allowed_hosts.split(",") if host.strip()
        )

    @property
    def candidates(self) -> int:
        """SQL candidates per Ask question (``ASK_CANDIDATES`` wins over ``SQL_CANDIDATES``)."""
        chosen = self.ask_candidates if self.ask_candidates is not None else self.sql_candidates
        return max(1, chosen)

    @property
    def category_values_allowed(self) -> bool:
        """Distinct category values may go to a model only at the ``balanced`` or ``open`` level.

        Values are masked by the identifier mask before they leave in any case.
        """
        return self.privacy_level in ("balanced", "open") and self.privacy_mode != "cloud"

    @property
    def max_result_rows_to_llm(self) -> int:
        """Rows of query results the LLM may see: only at the ``open`` level (decision D8)."""
        if self.privacy_mode == "cloud" or self.privacy_level != "open":
            return 0
        return 50


def _tiers(fast: str, main: str, deep: str) -> dict[str, str]:
    """Fill missing tiers from ``main`` (then from ``fast``); empty dict when none is set."""
    main = main.strip() or fast.strip() or deep.strip()
    if not main:
        return {}
    return {"fast": fast.strip() or main, "main": main, "deep": deep.strip() or main}


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
