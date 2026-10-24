"""Runtime settings for the orchestrator service.

Values come from environment variables (optionally mirrored in a local
``.env`` file, which is git-ignored). No secret is ever given a default that
could reach production: an empty ``BEARER_TOKEN`` means the HTTP layer fails
closed and rejects every authenticated request until a token is configured.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Process settings bound to the variable names documented in ``.env.example``."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    database_path: Path = Path("data/aetherflow.db")
    bearer_token: str = ""
    log_level: str = "INFO"


@lru_cache
def get_settings() -> Settings:
    """Return a cached :class:`Settings` instance.

    Tests clear the cache after mutating the environment so each fixture
    observes its own values.
    """
    return Settings()
