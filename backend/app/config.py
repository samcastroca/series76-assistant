"""Application settings. The only module that reads environment variables."""

from functools import lru_cache
from typing import Literal

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(extra="ignore")

    database_url: str = "postgresql+asyncpg://series76:series76@localhost:5432/series76"

    # Ingest `extract` stage only (human-run): vision model via Google AI Studio or OpenRouter.
    # INGEST_OPENROUTER_API_KEY is a separate key; OPENROUTER_API_KEY is never read here.
    ingest_llm_provider: Literal["google", "openrouter"] = "google"
    ingest_llm_model: str | None = None
    google_api_key: SecretStr | None = None
    ingest_openrouter_api_key: SecretStr | None = None


@lru_cache
def get_settings() -> Settings:
    return Settings()
