"""Phase 2 exit criteria: a session survives the round trip into the warehouse.

These tests drive SessionLoader against the live database with a synthetic source
object rather than a live FastF1 download, so they assert the persistence contract —
idempotent replacement, referential integrity, correct unit conversion — without
depending on network access or a warm cache.
"""

from __future__ import annotations

import datetime as dt
from types import SimpleNamespace

import pandas as pd
import pytest
from sqlalchemy import text
from sqlalchemy.orm import sessionmaker

from f1x.ingest.fastf1_client import SessionRequest
from f1x.ingest.loader import SessionLoader

pytestmark = pytest.mark.integration

# A real-era season with no FastF1 coverage, so these tests never collide with
# ingested data or with each other.
TEST_YEAR = 1951
T0 = dt.datetime(2023, 3, 5, 15, 0, tzinfo=dt.UTC)


def _source(*, n_laps: int = 3) -> SimpleNamespace:
    """A minimal stand-in exposing the attributes SessionLoader actually reads."""
    laps = pd.DataFrame(
        {
            "Driver": ["HAM"] * n_laps,
            "DriverNumber": ["44"] * n_laps,
            "LapNumber": list(range(1, n_laps + 1)),
            "LapTime": [pd.Timedelta(90.0 + i, unit="s") for i in range(n_laps)],
            "Stint": [1.0] * n_laps,
            "Compound": ["SOFT"] * n_laps,
            "TyreLife": [float(i + 1) for i in range(n_laps)],
            "TrackStatus": ["1"] * n_laps,
            "Position": [1.0] * n_laps,
            "Deleted": [False] * n_laps,
            "IsAccurate": [True] * n_laps,
            "LapStartTime": [pd.Timedelta(100.0 + i * 90, unit="s") for i in range(n_laps)],
            "LapStartDate": [T0 + dt.timedelta(seconds=100 + i * 90) for i in range(n_laps)],
        }
    )
    return SimpleNamespace(
        drivers=["44"],
        laps=laps,
        results=pd.DataFrame(),
        weather_data=pd.DataFrame(
            {
                "Time": [pd.Timedelta(60.0, unit="s")],
                "AirTemp": [24.5],
                "TrackTemp": [31.0],
                "Humidity": [40.0],
                "Pressure": [1010.0],
                "WindSpeed": [2.0],
                "WindDirection": [180],
                "Rainfall": [False],
            }
        ),
        race_control_messages=pd.DataFrame(
            {
                "Time": [T0 + dt.timedelta(seconds=30)],
                "Category": ["Flag"],
                "Message": ["GREEN LIGHT"],
                "Flag": ["GREEN"],
            }
        ),
        car_data={},
        pos_data={},
        event=pd.Series(
            {
                "EventName": "Synthetic Grand Prix",
                "OfficialEventName": "Synthetic GP",
                "Country": "Testland",
                "Location": "Testville",
                "EventDate": pd.Timestamp("2023-03-05"),
                "EventFormat": "conventional",
                "CircuitKey": "synthetic",
                "CircuitShortName": "Synthetic",
            }
        ),
        date=T0,
        t0_date=T0,
        name="Race",
        total_laps=n_laps,
        get_driver=lambda _n: pd.Series(
            {
                "DriverId": "test_driver",
                "Abbreviation": "TST",
                "FirstName": "Test",
                "LastName": "Driver",
                "TeamName": "Test Team",
                "TeamId": "test_team",
                "TeamColor": "FF0000",
            }
        ),
    )


@pytest.fixture
def loader(db_engine) -> SessionLoader:
    return SessionLoader(sessionmaker(db_engine, expire_on_commit=False))


@pytest.fixture(autouse=True)
def _cleanup(db_engine):
    """Remove the synthetic season before and after each test."""

    def purge() -> None:
        with db_engine.begin() as conn:
            conn.execute(text("DELETE FROM core.seasons WHERE year = :y"), {"y": TEST_YEAR})
            conn.execute(
                text("DELETE FROM raw.ingest_runs WHERE season_year = :y"), {"y": TEST_YEAR}
            )

    purge()
    yield
    purge()


def _request(**kw: object) -> SessionRequest:
    return SessionRequest(year=TEST_YEAR, round_number=1, kind="R", telemetry=False, **kw)  # type: ignore[arg-type]


def test_session_persists_with_expected_counts(loader: SessionLoader, db_engine) -> None:
    summary = loader.persist(_request(), _source())

    assert summary.laps == 3
    assert summary.drivers == 1
    assert summary.weather == 1
    assert summary.race_control == 1

    with db_engine.connect() as conn:
        laps = conn.execute(
            text("SELECT count(*) FROM core.laps WHERE session_id = :s"),
            {"s": summary.session_id},
        ).scalar_one()
    assert laps == 3


def test_lap_times_are_stored_as_seconds(loader: SessionLoader, db_engine) -> None:
    """A 90-second lap must land as 90.0, not as a nanosecond count or an interval."""
    summary = loader.persist(_request(), _source())

    with db_engine.connect() as conn:
        first = conn.execute(
            text(
                "SELECT lap_time_s FROM core.laps "
                "WHERE session_id = :s AND lap_number = 1"
            ),
            {"s": summary.session_id},
        ).scalar_one()
    assert first == pytest.approx(90.0)


def test_reingestion_replaces_rather_than_duplicates(loader: SessionLoader, db_engine) -> None:
    """The whole point of delete-then-insert: running twice must not double the data."""
    first = loader.persist(_request(), _source(n_laps=3))
    second = loader.persist(_request(), _source(n_laps=5))

    assert second.session_id != first.session_id

    with db_engine.connect() as conn:
        sessions = conn.execute(
            text(
                "SELECT count(*) FROM core.sessions s "
                "JOIN core.events e ON e.id = s.event_id "
                "WHERE e.season_year = :y"
            ),
            {"y": TEST_YEAR},
        ).scalar_one()
        laps = conn.execute(
            text("SELECT count(*) FROM core.laps WHERE session_id = :s"),
            {"s": second.session_id},
        ).scalar_one()
        stale = conn.execute(
            text("SELECT count(*) FROM core.laps WHERE session_id = :s"),
            {"s": first.session_id},
        ).scalar_one()

    assert sessions == 1, "re-ingesting must replace the session, not add one"
    assert laps == 5
    assert stale == 0, "laps from the replaced session must be gone"


def test_every_ingestion_appends_an_audit_row(loader: SessionLoader, db_engine) -> None:
    """raw.ingest_runs is append-only: replacing core data still records both runs."""
    loader.persist(_request(), _source())
    loader.persist(_request(), _source())

    with db_engine.connect() as conn:
        runs = conn.execute(
            text("SELECT count(*) FROM raw.ingest_runs WHERE season_year = :y"),
            {"y": TEST_YEAR},
        ).scalar_one()
    assert runs == 2


def test_quality_gate_blocks_duplicate_laps(loader: SessionLoader) -> None:
    """A bad source must fail before it reaches the database."""
    from f1x.ingest.exceptions import DataQualityError

    source = _source()
    source.laps = pd.concat([source.laps, source.laps.iloc[[0]]], ignore_index=True)

    with pytest.raises(DataQualityError, match="duplicate"):
        loader.persist(_request(), source)


def test_failed_ingestion_leaves_no_partial_rows(loader: SessionLoader, db_engine) -> None:
    """A mid-write failure must roll back, not leave a half-written session behind."""
    source = _source()
    # Break weather conversion after laps would already have been written.
    source.weather_data = pd.DataFrame({"Time": ["not-a-timedelta"], "AirTemp": [20.0]})

    with pytest.raises(Exception):  # noqa: B017 - any failure must still roll back
        loader.persist(_request(), source)

    with db_engine.connect() as conn:
        sessions = conn.execute(
            text(
                "SELECT count(*) FROM core.sessions s "
                "JOIN core.events e ON e.id = s.event_id "
                "WHERE e.season_year = :y"
            ),
            {"y": TEST_YEAR},
        ).scalar_one()
    assert sessions == 0
