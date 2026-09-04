"""Application configuration."""

from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Environment-backed application settings."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+psycopg://disclosure:disclosure_dev@localhost:5432/disclosure"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the cached process settings."""

    return Settings()
