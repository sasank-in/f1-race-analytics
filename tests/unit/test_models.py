"""Model definitions — checked without a database."""

from __future__ import annotations

import pytest

from f1x.models import HYPERTABLES, Base, Lap, Session, Telemetry


def test_all_tables_registered() -> None:
    names = set(Base.metadata.tables)
    assert "core.laps" in names
    assert "core.telemetry" in names
    assert "mart.lap_metrics" in names


def test_derived_tables_live_in_mart() -> None:
    """Engine output must never sit in core, which holds only what a source reports."""
    assert Base.metadata.tables["mart.lap_metrics"].schema == "mart"


@pytest.mark.parametrize("table", list(HYPERTABLES))
def test_hypertable_pk_includes_time_column(table: str) -> None:
    """Timescale requires the partitioning column in every unique index."""
    time_col, _ = HYPERTABLES[table]
    pk = {c.name for c in Base.metadata.tables[table].primary_key.columns}
    assert time_col in pk, f"{table} primary key must contain {time_col}"


@pytest.mark.parametrize("table", list(HYPERTABLES))
def test_hypertable_chunk_interval_declared(table: str) -> None:
    _, interval = HYPERTABLES[table]
    assert interval.split()[0].isdigit()


def test_race_control_pk_has_tiebreaker() -> None:
    """Several messages can share a timestamp, so ts+session alone is not unique."""
    from f1x.models import RaceControl

    pk = {c.name for c in RaceControl.__table__.primary_key.columns}
    assert "seq" in pk


def test_lap_times_stored_as_float_seconds() -> None:
    """Regressions and deltas operate on floats; INTERVAL would cast on every read."""
    assert Lap.__table__.c.lap_time_s.type.python_type is float


def test_telemetry_uses_narrow_types() -> None:
    """Width matters at ~87M rows per season: 4-byte reals, not 8-byte doubles."""
    from sqlalchemy.dialects import postgresql

    pg = postgresql.dialect()
    cols = Telemetry.__table__.c
    assert cols.speed.type.compile(pg) == "REAL"
    assert cols.rpm.type.compile(pg) == "REAL"
    assert cols.throttle.type.compile(pg) == "REAL"
    assert cols.gear.type.compile(pg) == "SMALLINT"


def test_track_status_is_text_not_numeric() -> None:
    """FastF1 concatenates status codes, e.g. '2671' means several applied in one lap."""
    assert Lap.__table__.c.track_status.type.python_type is str


def test_session_has_t0_for_time_alignment() -> None:
    """Telemetry timestamps are session-relative until anchored to this."""
    assert "t0_utc" in Session.__table__.c


def test_laps_cascade_on_session_delete() -> None:
    fk = next(iter(Lap.__table__.c.session_id.foreign_keys))
    assert fk.ondelete == "CASCADE"
