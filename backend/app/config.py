"""Application settings. The only module that reads environment variables."""

from decimal import Decimal
from functools import lru_cache
from typing import Literal, Self

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(extra="ignore")

    database_url: str = "postgresql+asyncpg://series76:series76@localhost:5432/series76"

    # Agent. Model IDs come only from the environment; there is no default model in code.
    # `fake` is a scripted LangChain model that calls the real tools (tests, CI, `make up`).
    llm_provider: Literal["openrouter", "google", "fake"] = "fake"
    agent_model: str | None = None
    fallback_model: str | None = None
    openrouter_api_key: SecretStr | None = None
    llm_max_tokens: int = 1500  # output cap; reasoning models spend part of it (ADR 0005)
    llm_timeout_s: float = 60.0
    model_call_limit: int = 6  # model calls per turn
    tool_call_limit: int = 6  # tool calls per turn
    history_turns: int = 10
    max_query_chars: int = 1000
    # Daily LLM spend cap in USD (UTC day, from llm_usage.cost_usd). Unset: no cap. Over it, a
    # turn answers 503 budget_exceeded before calling the model.
    daily_budget_usd: Decimal | None = Field(default=None, ge=0)

    # Auth. JWT_SECRET has no default in code: create_app refuses to start without one (ADR 0006).
    jwt_secret: SecretStr | None = None
    jwt_expire_minutes: int = Field(default=30, gt=0)
    login_max_attempts: int = Field(default=5, gt=0)  # failed logins per (IP, email) and window
    login_window_minutes: int = Field(default=15, gt=0)
    # Demo users created by `python -m app.db.seed`. Dev defaults live in docker-compose.yml only.
    seed_engineer1_password: SecretStr | None = None
    seed_engineer2_password: SecretStr | None = None
    seed_developer_password: SecretStr | None = None

    # Ingest `extract` stage only (human-run): vision model via Google AI Studio or OpenRouter.
    # INGEST_OPENROUTER_API_KEY is a separate key; OPENROUTER_API_KEY is never read here.
    ingest_llm_provider: Literal["google", "openrouter"] = "google"
    ingest_llm_model: str | None = None
    google_api_key: SecretStr | None = None
    ingest_openrouter_api_key: SecretStr | None = None

    @field_validator(
        "agent_model",
        "fallback_model",
        "openrouter_api_key",
        "google_api_key",
        "daily_budget_usd",
        "jwt_secret",
        "seed_engineer1_password",
        "seed_engineer2_password",
        "seed_developer_password",
        "ingest_llm_model",
        "ingest_openrouter_api_key",
        mode="before",
    )
    @classmethod
    def _blank_is_unset(cls, value: object) -> object:
        return None if value == "" else value  # compose passes unset variables as ""

    @model_validator(mode="after")
    def _real_provider_is_configured(self) -> Self:
        if self.llm_provider == "fake":
            return self
        if not self.agent_model:
            raise ValueError(f"LLM_PROVIDER={self.llm_provider} requires AGENT_MODEL")
        key = self.openrouter_api_key if self.llm_provider == "openrouter" else self.google_api_key
        if key is None:
            name = "OPENROUTER_API_KEY" if self.llm_provider == "openrouter" else "GOOGLE_API_KEY"
            raise ValueError(f"LLM_PROVIDER={self.llm_provider} requires {name}")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
