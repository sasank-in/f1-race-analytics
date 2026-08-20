"""Settings and package wiring."""

from __future__ import annotations

from f1x.config import ENGINE_VERSION, ROOT, Settings, get_settings


def test_root_points_at_repo() -> None:
    assert (ROOT / "ARCHITECTURE.md").is_file()


def test_database_url_uses_psycopg_driver() -> None:
    url = str(Settings().database_url)
    assert url.startswith("postgresql+psycopg://")


def test_database_url_avoids_local_postgres_port() -> None:
    # 5432 belongs to the host's own PostgreSQL install; the container maps to 5433.
    assert Settings().db_port != 5432


def test_settings_are_cached() -> None:
    assert get_settings() is get_settings()


def test_engine_version_is_semver() -> None:
    parts = ENGINE_VERSION.split(".")
    assert len(parts) == 3
    assert all(p.isdigit() for p in parts)


def test_telemetry_era_starts_2018() -> None:
    # FastF1 exposes car telemetry only from 2018 onward.
    assert Settings().telemetry_first_season == 2018
