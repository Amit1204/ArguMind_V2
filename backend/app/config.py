"""Application settings, sourced from environment variables only.

Every value has a local default so the service starts inside Docker Compose
without a `.env` file. Secrets are read from the environment and never logged.
"""

from __future__ import annotations

from functools import lru_cache

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(extra="ignore", case_sensitive=False)

    app_name: str = "ArguMind"
    app_version: str = "0.1.0"
    app_env: str = Field(default="development", pattern="^(development|test|staging|production)$")
    log_level: str = "INFO"
    # json: one structured object per line (default); text: classic single line.
    log_format: str = Field(default="json", pattern="^(json|text)$")

    database_url: str = (
        "postgresql+psycopg://argumind:argumind_dev_password@localhost:5433/argumind"
    )
    db_pool_size: int = Field(default=5, ge=1)
    db_max_overflow: int = Field(default=5, ge=0)
    db_pool_timeout_seconds: int = Field(default=10, ge=1)
    db_connect_timeout_seconds: int = Field(default=5, ge=1)

    cors_origins: str = "http://localhost:3100"

    # --- LLM ---------------------------------------------------------------
    llm_provider: str = Field(default="gemini", pattern="^(gemini|mock)$")
    llm_api_key: SecretStr = SecretStr("")
    llm_model_fast: str = "gemini-3.5-flash-lite"
    llm_model_standard: str = "gemini-3-flash-preview"
    llm_timeout_seconds: int = Field(default=45, ge=5, le=300)
    # Local guard so a runaway loop cannot exhaust the provider's daily quota.
    llm_daily_request_limit: int = Field(default=1000, ge=1)

    # --- Pipeline ----------------------------------------------------------
    run_timeout_seconds: int = Field(default=180, ge=10, le=900)

    @field_validator("log_level")
    @classmethod
    def _upper_level(cls, value: str) -> str:
        level = value.upper()
        if level not in {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}:
            raise ValueError(f"unsupported log level: {value}")
        return level

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def llm_configured(self) -> bool:
        """True when the configured provider can actually be called."""
        return self.llm_provider == "mock" or bool(self.llm_api_key.get_secret_value())


@lru_cache
def get_settings() -> Settings:
    return Settings()
