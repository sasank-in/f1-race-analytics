"""Materialise a validated FastF1 session into the immutable/raw and core layers."""

# ruff: noqa: ANN401

from __future__ import annotations

import datetime as dt
import hashlib
import json
import re
from collections.abc import Iterable
from dataclasses import asdict, dataclass
from typing import Any, cast

import pandas as pd  # type: ignore[import-untyped]
from fastf1.exceptions import DataNotLoadedError
from sqlalchemy import delete, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session, sessionmaker

from f1x.ingest.fastf1_client import SessionRequest
from f1x.ingest.quality import QualityReport, validate_session
from f1x.models import (
    Circuit,
    Driver,
    Entry,
    Event,
    Lap,
    Position,
    RaceControl,
    Result,
    Season,
    Team,
    Telemetry,
    Weather,
)
from f1x.models import Session as RaceSession

INSERT_BATCH_SIZE = 10_000


@dataclass(frozen=True)
class IngestSummary:
    """Counts written by one atomic session ingestion."""

    session_id: int
    drivers: int
    laps: int
    telemetry: int
    positions: int
    weather: int
    race_control: int
    quality: QualityReport


def _value(record: Any, name: str, default: Any = None) -> Any:
    """Read a FastF1 Series, mapping, or object without leaking pandas nulls."""
    try:
        value = record.get(name, default) if hasattr(record, "get") else getattr(record, name)
    except (AttributeError, KeyError):
        return default
    return default if value is None or pd.isna(value) else value


def _seconds(value: Any) -> float | None:
    if value is None or pd.isna(value):
        return None
    return float(pd.Timedelta(value).total_seconds())


def _relative_seconds(value: Any, t0: dt.datetime) -> float | None:
    """Convert either FastF1's relative timedelta or absolute message time."""
    if isinstance(value, (dt.datetime, pd.Timestamp)):
        stamp = _timestamp(value)
        return (stamp - t0).total_seconds() if stamp is not None else None
    return _seconds(value)


def _timestamp(value: Any) -> dt.datetime | None:
    if value is None or pd.isna(value):
        return None
    stamp = pd.Timestamp(value)
    if stamp.tzinfo is None:
        stamp = stamp.tz_localize("UTC")
    return cast(dt.datetime, stamp.to_pydatetime())


def _number(value: Any) -> float | None:
    if value is None or pd.isna(value):
        return None
    return float(value)


def _integer(value: Any) -> int | None:
    if value is None or pd.isna(value):
        return None
    return int(value)


def _boolean(value: Any) -> bool | None:
    if value is None or pd.isna(value):
        return None
    return bool(value)


def _slug(value: Any, *, fallback: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", str(value or fallback).lower()).strip("-")
    return slug[:64] or fallback


def _frame_digest(frame: Any) -> str | None:
    if not isinstance(frame, pd.DataFrame):
        return None
    encoded = frame.to_json(orient="split", date_format="iso", date_unit="ns")
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _insert_in_batches(
    db: Session, table: Any, rows: Iterable[dict[str, Any]], *, batch_size: int = INSERT_BATCH_SIZE
) -> int:
    """Insert an arbitrary-size stream without constructing a season-sized parameter list."""
    batch: list[dict[str, Any]] = []
    inserted = 0
    for row in rows:
        batch.append(row)
        if len(batch) == batch_size:
            db.execute(insert(table), batch)
            inserted += len(batch)
            batch.clear()
    if batch:
        db.execute(insert(table), batch)
        inserted += len(batch)
    return inserted


def _session_t0(source: Any) -> dt.datetime | None:
    """Resolve FastF1's session origin, including no-telemetry loads.

    ``Session.t0_date`` is only populated when telemetry is requested. The lap
    feed contains the same alignment information, so use its earliest absolute
    lap timestamp minus its session-relative start as the fallback.
    """
    try:
        telemetry_t0 = _timestamp(source.t0_date)
    except (AttributeError, DataNotLoadedError):
        # FastF1 raises DataNotLoadedError when telemetry was skipped.
        telemetry_t0 = None
    if telemetry_t0 is not None:
        return telemetry_t0

    laps = getattr(source, "laps", None)
    if isinstance(laps, pd.DataFrame):
        candidates: list[dt.datetime] = []
        for _, lap in laps.iterrows():
            lap_start = _timestamp(_value(lap, "LapStartDate"))
            lap_start_s = _seconds(_value(lap, "LapStartTime"))
            if lap_start is not None and lap_start_s is not None:
                candidates.append(lap_start - dt.timedelta(seconds=lap_start_s))
        if candidates:
            return min(candidates)
    return _timestamp(getattr(source, "date", None))


class SessionLoader:
    """Write one FastF1 session atomically, replacing its conformed projection.

    The original source manifest is appended to ``raw.ingest_runs`` before the
    projection is written. Re-ingestion creates a new immutable audit entry and
    replaces only the corresponding rows in ``core``.
    """

    def __init__(self, sessions: sessionmaker[Session]) -> None:
        self._sessions = sessions

    def persist(self, request: SessionRequest, source: Any) -> IngestSummary:
        report = validate_session(source)
        with self._sessions.begin() as db:
            self._record_raw_run(db, request, source, report)
            event_id = self._upsert_event(db, request, source)
            db.execute(
                delete(RaceSession).where(
                    RaceSession.event_id == event_id,
                    RaceSession.kind == request.kind,
                )
            )
            session_id, t0 = self._insert_session(db, event_id, request, source)
            driver_ids = self._persist_entries(db, session_id, source)
            self._persist_results(db, session_id, source, driver_ids)
            lap_count = self._persist_laps(db, session_id, source)
            weather_count = self._persist_weather(db, session_id, source, t0)
            message_count = self._persist_race_control(db, session_id, source, t0)
            telemetry_count, position_count = (
                self._persist_traces(db, session_id, source, t0) if request.telemetry else (0, 0)
            )

        return IngestSummary(
            session_id=session_id,
            drivers=report.drivers,
            laps=lap_count,
            telemetry=telemetry_count,
            positions=position_count,
            weather=weather_count,
            race_control=message_count,
            quality=report,
        )

    def _record_raw_run(
        self, db: Session, request: SessionRequest, source: Any, report: QualityReport
    ) -> None:
        frames = {
            name: _frame_digest(getattr(source, name, None))
            for name in ("results", "laps", "weather_data", "race_control_messages")
        }
        payload = {
            "request": asdict(request),
            "event": {
                key: str(_value(source.event, key))
                for key in ("EventName", "OfficialEventName", "Country", "Location", "EventDate")
            },
            "quality": asdict(report),
            "frame_sha256": frames,
        }
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        db.execute(
            text(
                "INSERT INTO raw.ingest_runs "
                "(source, season_year, round_number, session_kind, payload, payload_sha256) "
                "VALUES ('fastf1', :year, :round, :kind, CAST(:payload AS jsonb), :digest)"
            ),
            {
                "year": request.year,
                "round": request.round_number,
                "kind": request.kind,
                "payload": encoded,
                "digest": hashlib.sha256(encoded.encode("utf-8")).hexdigest(),
            },
        )

    def _upsert_event(self, db: Session, request: SessionRequest, source: Any) -> int:
        event = source.event
        telemetry_available = request.year >= 2018
        db.execute(
            insert(Season)
            .values(year=request.year, has_telemetry=telemetry_available)
            .on_conflict_do_update(
                index_elements=[Season.year],
                set_={"has_telemetry": Season.has_telemetry | telemetry_available},
            )
        )
        circuit_name = _value(event, "CircuitShortName", _value(event, "Location", "unknown"))
        circuit_key = _slug(_value(event, "CircuitKey", circuit_name), fallback="unknown-circuit")
        circuit_id = db.execute(
            insert(Circuit)
            .values(
                key=circuit_key,
                name=str(circuit_name),
                locality=_value(event, "Location"),
                country=_value(event, "Country"),
            )
            .on_conflict_do_update(
                index_elements=[Circuit.key],
                set_={
                    "name": str(circuit_name),
                    "locality": _value(event, "Location"),
                    "country": _value(event, "Country"),
                },
            )
            .returning(Circuit.id)
        ).scalar_one()
        event_date = _timestamp(_value(event, "EventDate"))
        return db.execute(
            insert(Event)
            .values(
                season_year=request.year,
                round=request.round_number,
                circuit_id=circuit_id,
                name=str(_value(event, "EventName", f"Round {request.round_number}")),
                official_name=_value(event, "OfficialEventName"),
                country=_value(event, "Country"),
                location=_value(event, "Location"),
                event_date=event_date.date() if event_date else None,
                format=_value(event, "EventFormat"),
            )
            .on_conflict_do_update(
                constraint="uq_events_season_round",
                set_={
                    "circuit_id": circuit_id,
                    "name": str(_value(event, "EventName")),
                    "official_name": _value(event, "OfficialEventName"),
                    "country": _value(event, "Country"),
                    "location": _value(event, "Location"),
                    "event_date": event_date.date() if event_date else None,
                    "format": _value(event, "EventFormat"),
                },
            )
            .returning(Event.id)
        ).scalar_one()

    def _insert_session(
        self, db: Session, event_id: int, request: SessionRequest, source: Any
    ) -> tuple[int, dt.datetime | None]:
        t0 = _session_t0(source)
        start = _timestamp(getattr(source, "date", None))
        result = db.execute(
            insert(RaceSession)
            .values(
                event_id=event_id,
                kind=request.kind,
                name=getattr(source, "name", request.kind),
                start_utc=start,
                t0_utc=t0,
                total_laps=_integer(getattr(source, "total_laps", None)),
                laps_loaded=True,
                telemetry_loaded=request.telemetry,
                weather_loaded=True,
                ingested_at=dt.datetime.now(dt.UTC),
            )
            .returning(RaceSession.id)
        )
        return result.scalar_one(), t0

    def _persist_entries(self, db: Session, session_id: int, source: Any) -> dict[str, int]:
        driver_ids: dict[str, int] = {}
        for number in source.drivers:
            info = source.get_driver(number)
            driver_key = str(_value(info, "DriverId", _value(info, "Abbreviation", number)))
            team_name = _value(info, "TeamName")
            team_key = _slug(_value(info, "TeamId", team_name), fallback="unknown-team")
            team_id: int | None = None
            if team_name:
                team_id = db.execute(
                    insert(Team)
                    .values(key=team_key, name=str(team_name))
                    .on_conflict_do_update(index_elements=[Team.key], set_={"name": str(team_name)})
                    .returning(Team.id)
                ).scalar_one()
            driver_id = db.execute(
                insert(Driver)
                .values(
                    key=driver_key,
                    abbreviation=_value(info, "Abbreviation"),
                    first_name=_value(info, "FirstName"),
                    last_name=_value(info, "LastName"),
                    country_code=_value(info, "CountryCode"),
                )
                .on_conflict_do_update(
                    index_elements=[Driver.key],
                    set_={
                        "abbreviation": _value(info, "Abbreviation"),
                        "first_name": _value(info, "FirstName"),
                        "last_name": _value(info, "LastName"),
                        "country_code": _value(info, "CountryCode"),
                    },
                )
                .returning(Driver.id)
            ).scalar_one()
            driver_number = str(_value(info, "DriverNumber", number))
            db.execute(
                insert(Entry).values(
                    session_id=session_id,
                    driver_id=driver_id,
                    team_id=team_id,
                    driver_number=driver_number,
                    team_colour=_value(info, "TeamColor"),
                )
            )
            driver_ids[driver_number] = driver_id
        return driver_ids

    def _persist_results(
        self, db: Session, session_id: int, source: Any, driver_ids: dict[str, int]
    ) -> None:
        results = getattr(source, "results", pd.DataFrame())
        if not isinstance(results, pd.DataFrame):
            return
        rows: list[dict[str, Any]] = []
        for _, row in results.iterrows():
            number = str(_value(row, "DriverNumber", ""))
            driver_id = driver_ids.get(number)
            if driver_id is None:
                continue
            rows.append(
                {
                    "session_id": session_id,
                    "driver_id": driver_id,
                    "team_id": None,
                    "position": _number(_value(row, "Position")),
                    "classified_position": _value(row, "ClassifiedPosition"),
                    "grid_position": _number(_value(row, "GridPosition")),
                    "points": _number(_value(row, "Points")),
                    "laps_completed": _number(_value(row, "Laps")),
                    "status": _value(row, "Status"),
                    "total_time_s": _seconds(_value(row, "Time")),
                    "q1_s": _seconds(_value(row, "Q1")),
                    "q2_s": _seconds(_value(row, "Q2")),
                    "q3_s": _seconds(_value(row, "Q3")),
                }
            )
        if rows:
            _insert_in_batches(db, Result, rows)

    def _persist_laps(self, db: Session, session_id: int, source: Any) -> int:
        rows: list[dict[str, Any]] = []
        for _, row in source.laps.iterrows():
            lap_number = _integer(_value(row, "LapNumber"))
            if lap_number is None:
                continue
            rows.append(
                {
                    "session_id": session_id,
                    "driver_number": str(_value(row, "DriverNumber")),
                    "lap_number": lap_number,
                    "lap_time_s": _seconds(_value(row, "LapTime")),
                    "sector1_s": _seconds(_value(row, "Sector1Time")),
                    "sector2_s": _seconds(_value(row, "Sector2Time")),
                    "sector3_s": _seconds(_value(row, "Sector3Time")),
                    "lap_start_s": _seconds(_value(row, "LapStartTime")),
                    "lap_end_s": _seconds(_value(row, "Time")),
                    "lap_start_utc": _timestamp(_value(row, "LapStartDate")),
                    "speed_i1": _number(_value(row, "SpeedI1")),
                    "speed_i2": _number(_value(row, "SpeedI2")),
                    "speed_fl": _number(_value(row, "SpeedFL")),
                    "speed_st": _number(_value(row, "SpeedST")),
                    "stint": _integer(_value(row, "Stint")),
                    "compound": _value(row, "Compound"),
                    "tyre_life": _number(_value(row, "TyreLife")),
                    "fresh_tyre": _boolean(_value(row, "FreshTyre")),
                    "pit_in_s": _seconds(_value(row, "PitInTime")),
                    "pit_out_s": _seconds(_value(row, "PitOutTime")),
                    "position": _number(_value(row, "Position")),
                    "track_status": _value(row, "TrackStatus"),
                    "is_personal_best": _boolean(_value(row, "IsPersonalBest")),
                    "deleted": _boolean(_value(row, "Deleted")) or False,
                    "deleted_reason": _value(row, "DeletedReason"),
                    "is_accurate": _boolean(_value(row, "IsAccurate")) or False,
                }
            )
        return _insert_in_batches(db, Lap, rows)

    def _persist_weather(
        self, db: Session, session_id: int, source: Any, t0: dt.datetime | None
    ) -> int:
        weather = getattr(source, "weather_data", pd.DataFrame())
        if not isinstance(weather, pd.DataFrame) or t0 is None:
            return 0
        rows = []
        for _, row in weather.iterrows():
            seconds = _seconds(_value(row, "Time")) or 0
            rows.append(
                {
                    "ts": t0 + dt.timedelta(seconds=seconds),
                    "session_id": session_id,
                    "session_s": seconds,
                    "air_temp": _number(_value(row, "AirTemp")),
                    "track_temp": _number(_value(row, "TrackTemp")),
                    "humidity": _number(_value(row, "Humidity")),
                    "pressure": _number(_value(row, "Pressure")),
                    "wind_speed": _number(_value(row, "WindSpeed")),
                    "wind_direction": _integer(_value(row, "WindDirection")),
                    "rainfall": _boolean(_value(row, "Rainfall")),
                }
            )
        return _insert_in_batches(db, Weather, rows)

    def _persist_race_control(
        self, db: Session, session_id: int, source: Any, t0: dt.datetime | None
    ) -> int:
        messages = getattr(source, "race_control_messages", pd.DataFrame())
        if not isinstance(messages, pd.DataFrame) or t0 is None:
            return 0
        rows = []
        for seq, (_, row) in enumerate(messages.iterrows()):
            seconds = _relative_seconds(_value(row, "Time"), t0)
            racing_number = _value(row, "RacingNumber")
            rows.append(
                {
                    "ts": t0 + dt.timedelta(seconds=seconds or 0),
                    "session_id": session_id,
                    "seq": seq,
                    "session_s": seconds,
                    "category": _value(row, "Category"),
                    "message": _value(row, "Message"),
                    "status": _value(row, "Status"),
                    "flag": _value(row, "Flag"),
                    "scope": _value(row, "Scope"),
                    "sector": _integer(_value(row, "Sector")),
                    "driver_number": str(racing_number) if racing_number else None,
                    "lap_number": _integer(_value(row, "Lap")),
                }
            )
        return _insert_in_batches(db, RaceControl, rows)

    def _persist_traces(
        self, db: Session, session_id: int, source: Any, t0: dt.datetime | None
    ) -> tuple[int, int]:
        if t0 is None:
            return 0, 0
        telemetry_rows: list[dict[str, Any]] = []
        position_rows: list[dict[str, Any]] = []
        telemetry_count = 0
        position_count = 0
        for driver in source.drivers:
            number = str(driver)
            for _, row in getattr(source, "car_data", {}).get(driver, pd.DataFrame()).iterrows():
                stamp = _timestamp(_value(row, "Date"))
                if stamp is None:
                    continue
                telemetry_rows.append(
                    {
                        "ts": stamp,
                        "session_id": session_id,
                        "driver_number": number,
                        "session_s": (stamp - t0).total_seconds(),
                        "speed": _number(_value(row, "Speed")),
                        "rpm": _number(_value(row, "RPM")),
                        "gear": _integer(_value(row, "nGear")),
                        "throttle": _number(_value(row, "Throttle")),
                        "brake": _boolean(_value(row, "Brake")),
                        "drs": _integer(_value(row, "DRS")),
                        "source": _value(row, "Source"),
                    }
                )
                if len(telemetry_rows) == INSERT_BATCH_SIZE:
                    db.execute(insert(Telemetry), telemetry_rows)
                    telemetry_count += len(telemetry_rows)
                    telemetry_rows.clear()
            for _, row in getattr(source, "pos_data", {}).get(driver, pd.DataFrame()).iterrows():
                stamp = _timestamp(_value(row, "Date"))
                if stamp is None:
                    continue
                position_rows.append(
                    {
                        "ts": stamp,
                        "session_id": session_id,
                        "driver_number": number,
                        "session_s": (stamp - t0).total_seconds(),
                        "x": _number(_value(row, "X")),
                        "y": _number(_value(row, "Y")),
                        "z": _number(_value(row, "Z")),
                        "status": _value(row, "Status"),
                    }
                )
                if len(position_rows) == INSERT_BATCH_SIZE:
                    db.execute(insert(Position), position_rows)
                    position_count += len(position_rows)
                    position_rows.clear()
        if telemetry_rows:
            db.execute(insert(Telemetry), telemetry_rows)
            telemetry_count += len(telemetry_rows)
        if position_rows:
            db.execute(insert(Position), position_rows)
            position_count += len(position_rows)
        return telemetry_count, position_count
