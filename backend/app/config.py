"""Application settings. The only module that reads environment variables."""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(extra="ignore")

    database_url: str = "postgresql+asyncpg://series76:series76@localhost:5432/series76"


@lru_cache
def get_settings() -> Settings:
    return Settings()
