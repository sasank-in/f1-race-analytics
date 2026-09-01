"""Application settings, loaded from the environment or a .env file."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field, PostgresDsn, RedisDsn, computed_field
from pydantic_settings import BaseSettings, SettingsConfigDict

# Repo root: .../f1-race (this file is backend/src/f1x/config.py).
ROOT = Path(__file__).resolve().parents[3]

# Bumped whenever a metric's definition changes. Stamped onto every derived row so
# stale materialisations and cached API responses invalidate on their own.
ENGINE_VERSION = "0.1.0"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(ROOT / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    env: str = "local"
    # DEBUG is commonly injected by shells and IDEs with non-boolean values such as
    # "release". Scope this setting to the application namespace to avoid collisions.
    debug: bool = Field(default=True, validation_alias="F1X_DEBUG")

    # --- database -------------------------------------------------------
    postgres_user: str = "f1x"
    postgres_password: str = "f1x"  # noqa: S105 - local dev default, overridden by .env
    postgres_db: str = "f1x"
    postgres_host: str = "localhost"
    # 5433, not 5432: the host already runs a local PostgreSQL 16 install.
    db_port: int = 5433

    # --- cache ----------------------------------------------------------
    redis_host: str = "localhost"
    redis_port: int = 6379

    # --- ingestion ------------------------------------------------------
    # FastF1 caches raw HTTP payloads here so re-ingesting a session is offline.
    fastf1_cache_dir: Path = Field(default=ROOT / ".cache" / "fastf1")
    # Telemetry exists from 2018; earlier seasons are results-only via Jolpica.
    telemetry_first_season: int = 2018

    @computed_field  # type: ignore[prop-decorator]
    @property
    def database_url(self) -> PostgresDsn:
        return PostgresDsn(
            f"postgresql+psycopg://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.db_port}/{self.postgres_db}"
        )

    @computed_field  # type: ignore[prop-decorator]
    @property
    def redis_url(self) -> RedisDsn:
        return RedisDsn(f"redis://{self.redis_host}:{self.redis_port}/0")


@lru_cache
def get_settings() -> Settings:
    """Cached accessor — settings are read once per process."""
    return Settings()
