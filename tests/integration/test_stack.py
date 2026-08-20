"""Phase 0 exit criteria: the Docker stack is up and correctly provisioned."""

from __future__ import annotations

import pytest
from sqlalchemy import text

pytestmark = pytest.mark.integration


def test_timescaledb_extension_installed(db_engine) -> None:
    with db_engine.connect() as conn:
        version = conn.execute(
            text("SELECT extversion FROM pg_extension WHERE extname = 'timescaledb'")
        ).scalar_one_or_none()
    assert version is not None, "timescaledb extension missing"
    assert int(version.split(".")[0]) >= 2


def test_schemas_exist(db_engine) -> None:
    with db_engine.connect() as conn:
        found = {
            row[0]
            for row in conn.execute(
                text(
                    "SELECT schema_name FROM information_schema.schemata "
                    "WHERE schema_name IN ('raw', 'core', 'mart')"
                )
            )
        }
    assert found == {"raw", "core", "mart"}


def test_hypertable_creation_works(db_engine) -> None:
    """Prove the Timescale API is usable, not just that the extension is loaded."""
    with db_engine.begin() as conn:
        conn.execute(text("DROP TABLE IF EXISTS core._ht_smoke"))
        conn.execute(
            text("CREATE TABLE core._ht_smoke (ts timestamptz NOT NULL, v double precision)")
        )
        conn.execute(text("SELECT create_hypertable('core._ht_smoke', 'ts')"))
        is_ht = conn.execute(
            text(
                "SELECT count(*) FROM timescaledb_information.hypertables "
                "WHERE hypertable_name = '_ht_smoke'"
            )
        ).scalar_one()
        conn.execute(text("DROP TABLE core._ht_smoke"))
    assert is_ht == 1


def test_redis_reachable() -> None:
    import redis

    from f1x.config import get_settings

    client = redis.Redis.from_url(str(get_settings().redis_url), socket_connect_timeout=3)
    assert client.ping() is True
