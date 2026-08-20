"""Shared pytest fixtures.

Unit tests run against recorded fixtures and never touch the database.
Integration tests (marked `integration`) use the live Docker stack.
"""

from __future__ import annotations

import os
import warnings
from collections.abc import Iterator
from pathlib import Path

import pytest

FIXTURE_DIR = Path(__file__).parent / "fixtures"


@pytest.fixture(scope="session")
def fixture_dir() -> Path:
    return FIXTURE_DIR


@pytest.fixture(scope="session")
def db_url() -> str:
    """URL of the running Docker stack."""
    from f1x.config import get_settings

    return str(get_settings().database_url)


@pytest.fixture(scope="session")
def db_engine(db_url: str) -> Iterator[object]:
    """SQLAlchemy engine against the live database. Fails loudly if unreachable."""
    from sqlalchemy import create_engine, text

    # Short timeout: an unreachable database should fail in seconds, not hang for
    # minutes on the default TCP timeout.
    engine = create_engine(
        db_url, pool_pre_ping=True, connect_args={"connect_timeout": 5}
    )
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
    except Exception as exc:  # noqa: BLE001
        # Skipping by default would let a real outage read as a green run, so require
        # an explicit opt-out for environments that genuinely have no database.
        if os.environ.get("F1X_SKIP_DB_TESTS"):
            pytest.skip(f"database not reachable (F1X_SKIP_DB_TESTS set): {exc}")
        pytest.fail(
            f"database not reachable at {db_url}: {exc}\n"
            "Start it with: docker compose -f docker/docker-compose.yml up -d\n"
            "Or set F1X_SKIP_DB_TESTS=1 to skip integration tests."
        )
    yield engine
    engine.dispose()


@pytest.fixture(scope="session", autouse=True)
def _fastf1_cache() -> None:
    """Point FastF1 at the repo cache so tests never re-download."""
    if os.environ.get("F1X_SKIP_CACHE"):
        return
    try:
        import fastf1

        from f1x.config import get_settings

        cache = get_settings().fastf1_cache_dir
        cache.mkdir(parents=True, exist_ok=True)
        fastf1.Cache.enable_cache(str(cache))
    except Exception as exc:  # noqa: BLE001 - cache is an optimisation, not a requirement
        warnings.warn(f"FastF1 cache unavailable: {exc}", RuntimeWarning, stacklevel=2)
