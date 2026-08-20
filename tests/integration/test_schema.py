"""Phase 1 exit criteria: the schema is applied and correctly provisioned."""

from __future__ import annotations

import pytest
from sqlalchemy import text

pytestmark = pytest.mark.integration

EXPECTED_CORE_TABLES = {
    "seasons", "circuits", "teams", "drivers", "events", "sessions", "entries",
    "results", "laps", "stints", "pit_stops",
    "telemetry", "positions", "weather", "race_control",
}
EXPECTED_HYPERTABLES = {"telemetry", "positions", "weather", "race_control"}
COMPRESSED = {"telemetry", "positions"}


def _scalars(conn, sql: str) -> set[str]:
    return {row[0] for row in conn.execute(text(sql))}


def test_core_tables_exist(db_engine) -> None:
    with db_engine.connect() as conn:
        found = _scalars(
            conn,
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_schema = 'core' AND table_type = 'BASE TABLE'",
        )
    assert found >= EXPECTED_CORE_TABLES


def test_mart_lap_metrics_exists(db_engine) -> None:
    with db_engine.connect() as conn:
        found = _scalars(
            conn,
            "SELECT table_name FROM information_schema.tables WHERE table_schema = 'mart'",
        )
    assert "lap_metrics" in found


def test_hypertables_registered(db_engine) -> None:
    with db_engine.connect() as conn:
        found = _scalars(
            conn, "SELECT hypertable_name FROM timescaledb_information.hypertables"
        )
    assert found >= EXPECTED_HYPERTABLES


def test_compression_enabled_on_large_hypertables(db_engine) -> None:
    """Only telemetry and positions justify compression; the small ones stay plain."""
    with db_engine.connect() as conn:
        rows = dict(
            conn.execute(
                text(
                    "SELECT hypertable_name, compression_enabled "
                    "FROM timescaledb_information.hypertables"
                )
            ).all()
        )
    for name in COMPRESSED:
        assert rows[name] is True, f"{name} should be compressed"


def test_compression_policies_scheduled(db_engine) -> None:
    with db_engine.connect() as conn:
        found = _scalars(
            conn,
            "SELECT hypertable_name FROM timescaledb_information.jobs "
            "WHERE proc_name = 'policy_compression'",
        )
    assert found >= COMPRESSED


def test_chunk_intervals(db_engine) -> None:
    """Telemetry chunks are daily; low-volume tables use a wider interval."""
    with db_engine.connect() as conn:
        rows = dict(
            conn.execute(
                text(
                    "SELECT hypertable_name, time_interval "
                    "FROM timescaledb_information.dimensions "
                    "WHERE hypertable_name IN "
                    "('telemetry', 'positions', 'weather', 'race_control')"
                )
            ).all()
        )
    assert str(rows["telemetry"]) == "1 day, 0:00:00"
    assert str(rows["positions"]) == "1 day, 0:00:00"
    assert str(rows["weather"]) == "7 days, 0:00:00"


def test_continuous_aggregate_exists(db_engine) -> None:
    with db_engine.connect() as conn:
        found = _scalars(
            conn, "SELECT view_name FROM timescaledb_information.continuous_aggregates"
        )
    assert "telemetry_lap_summary" in found


def test_enum_types_created(db_engine) -> None:
    with db_engine.connect() as conn:
        found = _scalars(
            conn,
            "SELECT t.typname FROM pg_type t JOIN pg_namespace n ON n.oid = t.typnamespace "
            "WHERE n.nspname = 'core' AND t.typtype = 'e'",
        )
    assert {"session_kind", "compound", "event_format"} <= found


def test_cascade_delete_from_session(db_engine) -> None:
    """Dropping a session must take its laps with it, or re-ingestion orphans rows."""
    with db_engine.begin() as conn:
        conn.execute(text("INSERT INTO core.seasons (year) VALUES (1999)"))
        event_id = conn.execute(
            text(
                "INSERT INTO core.events (season_year, round, name) "
                "VALUES (1999, 1, 'Test GP') RETURNING id"
            )
        ).scalar_one()
        session_id = conn.execute(
            text(
                "INSERT INTO core.sessions (event_id, kind) "
                "VALUES (:e, 'R') RETURNING id"
            ),
            {"e": event_id},
        ).scalar_one()
        conn.execute(
            text(
                "INSERT INTO core.laps (session_id, driver_number, lap_number) "
                "VALUES (:s, '44', 1)"
            ),
            {"s": session_id},
        )

        conn.execute(text("DELETE FROM core.sessions WHERE id = :s"), {"s": session_id})
        remaining = conn.execute(
            text("SELECT count(*) FROM core.laps WHERE session_id = :s"), {"s": session_id}
        ).scalar_one()

        conn.execute(text("DELETE FROM core.seasons WHERE year = 1999"))

    assert remaining == 0
