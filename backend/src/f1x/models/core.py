"""Conformed relational model (schema `core`).

Grain and provenance notes live next to the columns they explain. Anything derived by the
engine belongs in `mart`, not here — this schema holds only what a source reports.
"""

from __future__ import annotations

import datetime as dt

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    Double,
    Enum,
    ForeignKey,
    Index,
    Integer,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from f1x.models.base import Base

SessionKind = Enum(
    "FP1", "FP2", "FP3", "Q", "SQ", "S", "R",
    name="session_kind", schema="core", create_type=False,
)
Compound = Enum(
    "SOFT", "MEDIUM", "HARD", "INTERMEDIATE", "WET", "UNKNOWN", "TEST_UNKNOWN",
    name="compound", schema="core", create_type=False,
)
EventFormat = Enum(
    "conventional", "sprint", "sprint_shootout", "sprint_qualifying", "testing",
    name="event_format", schema="core", create_type=False,
)


class Season(Base):
    __tablename__ = "seasons"

    year: Mapped[int] = mapped_column(SmallInteger, primary_key=True)
    # Telemetry exists only from 2018; earlier seasons are results-only.
    has_telemetry: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))

    events: Mapped[list[Event]] = relationship(back_populates="season")


class Circuit(Base):
    __tablename__ = "circuits"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    key: Mapped[str] = mapped_column(String(64), unique=True)  # stable slug, e.g. "bahrain"
    name: Mapped[str] = mapped_column(Text)
    locality: Mapped[str | None] = mapped_column(Text)
    country: Mapped[str | None] = mapped_column(Text)
    lat: Mapped[float | None] = mapped_column(Double)
    lon: Mapped[float | None] = mapped_column(Double)

    # Circuit constants the strategy engine needs. Populated per-circuit in Phase 5;
    # nullable because they are measured from data, not supplied by any feed.
    lap_distance_m: Mapped[float | None] = mapped_column(Double)
    pit_lane_loss_s: Mapped[float | None] = mapped_column(
        Double, comment="Total time lost pitting vs staying out, excluding stationary time"
    )
    n_corners: Mapped[int | None] = mapped_column(SmallInteger)


class Team(Base):
    __tablename__ = "teams"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    key: Mapped[str] = mapped_column(String(64), unique=True)  # FastF1 TeamId
    name: Mapped[str] = mapped_column(Text)


class Driver(Base):
    __tablename__ = "drivers"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    key: Mapped[str] = mapped_column(String(64), unique=True)  # FastF1 DriverId
    abbreviation: Mapped[str | None] = mapped_column(String(3))
    first_name: Mapped[str | None] = mapped_column(Text)
    last_name: Mapped[str | None] = mapped_column(Text)
    country_code: Mapped[str | None] = mapped_column(String(3))


class Event(Base):
    """A grand prix weekend."""

    __tablename__ = "events"
    __table_args__ = (
        UniqueConstraint("season_year", "round", name="uq_events_season_round"),
        Index("ix_events_date", "event_date"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    season_year: Mapped[int] = mapped_column(
        ForeignKey("core.seasons.year", ondelete="CASCADE")
    )
    round: Mapped[int] = mapped_column(SmallInteger)
    circuit_id: Mapped[int | None] = mapped_column(ForeignKey("core.circuits.id"))
    name: Mapped[str] = mapped_column(Text)
    official_name: Mapped[str | None] = mapped_column(Text)
    country: Mapped[str | None] = mapped_column(Text)
    location: Mapped[str | None] = mapped_column(Text)
    event_date: Mapped[dt.date | None] = mapped_column(Date)
    format: Mapped[str | None] = mapped_column(EventFormat)

    season: Mapped[Season] = relationship(back_populates="events")
    sessions: Mapped[list[Session]] = relationship(back_populates="event")


class Session(Base):
    """One timed session. The hub every time-series table hangs off."""

    __tablename__ = "sessions"
    __table_args__ = (
        UniqueConstraint("event_id", "kind", name="uq_sessions_event_kind"),
        Index("ix_sessions_start", "start_utc"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    event_id: Mapped[int] = mapped_column(ForeignKey("core.events.id", ondelete="CASCADE"))
    kind: Mapped[str] = mapped_column(SessionKind)
    name: Mapped[str | None] = mapped_column(Text)
    start_utc: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))

    # Session-relative timedeltas are converted to absolute time using this offset,
    # so telemetry from different sessions can never collide on the time axis.
    t0_utc: Mapped[dt.datetime | None] = mapped_column(
        DateTime(timezone=True), comment="UTC wall-clock corresponding to session time zero"
    )
    total_laps: Mapped[int | None] = mapped_column(SmallInteger)

    # Ingestion bookkeeping — what actually loaded, and when.
    laps_loaded: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    telemetry_loaded: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    weather_loaded: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    ingested_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))

    event: Mapped[Event] = relationship(back_populates="sessions")


class Entry(Base):
    """A driver's entry for one session: the number/team pairing that applied at the time."""

    __tablename__ = "entries"
    __table_args__ = (
        UniqueConstraint("session_id", "driver_number", name="uq_entries_session_number"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    session_id: Mapped[int] = mapped_column(
        ForeignKey("core.sessions.id", ondelete="CASCADE"), index=True
    )
    driver_id: Mapped[int] = mapped_column(ForeignKey("core.drivers.id"))
    team_id: Mapped[int | None] = mapped_column(ForeignKey("core.teams.id"))
    # Text, not int: FastF1 keys telemetry dicts by the string form.
    driver_number: Mapped[str] = mapped_column(String(3))
    team_colour: Mapped[str | None] = mapped_column(String(9))


class Result(Base):
    __tablename__ = "results"
    __table_args__ = (
        UniqueConstraint("session_id", "driver_id", name="uq_results_session_driver"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    session_id: Mapped[int] = mapped_column(
        ForeignKey("core.sessions.id", ondelete="CASCADE"), index=True
    )
    driver_id: Mapped[int] = mapped_column(ForeignKey("core.drivers.id"))
    team_id: Mapped[int | None] = mapped_column(ForeignKey("core.teams.id"))

    position: Mapped[float | None] = mapped_column(Double)
    # Text because a DNF is reported as 'R', 'D', 'E', 'W', 'F', 'N' rather than a number.
    classified_position: Mapped[str | None] = mapped_column(String(4))
    grid_position: Mapped[float | None] = mapped_column(Double)
    points: Mapped[float | None] = mapped_column(Double)
    laps_completed: Mapped[float | None] = mapped_column(Double)
    status: Mapped[str | None] = mapped_column(Text)
    total_time_s: Mapped[float | None] = mapped_column(Double)

    # Qualifying segment times, seconds. Null outside qualifying sessions.
    q1_s: Mapped[float | None] = mapped_column(Double)
    q2_s: Mapped[float | None] = mapped_column(Double)
    q3_s: Mapped[float | None] = mapped_column(Double)


class Lap(Base):
    """One timed lap. The workhorse table for every pace and strategy metric."""

    __tablename__ = "laps"
    __table_args__ = (
        UniqueConstraint("session_id", "driver_number", "lap_number", name="uq_laps_key"),
        CheckConstraint("lap_number > 0", name="lap_number_positive"),
        # Pace queries filter on a session then scan valid green laps.
        Index("ix_laps_session_driver", "session_id", "driver_number", "lap_number"),
        Index("ix_laps_stint", "session_id", "driver_number", "stint"),
        {"comment": "Grain: one row per driver per lap per session"},
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    session_id: Mapped[int] = mapped_column(
        ForeignKey("core.sessions.id", ondelete="CASCADE")
    )
    driver_number: Mapped[str] = mapped_column(String(3))
    lap_number: Mapped[int] = mapped_column(SmallInteger)

    lap_time_s: Mapped[float | None] = mapped_column(Double)
    sector1_s: Mapped[float | None] = mapped_column(Double)
    sector2_s: Mapped[float | None] = mapped_column(Double)
    sector3_s: Mapped[float | None] = mapped_column(Double)

    # Session-relative seconds; needed to align laps with telemetry and weather.
    lap_start_s: Mapped[float | None] = mapped_column(Double)
    lap_end_s: Mapped[float | None] = mapped_column(Double)
    lap_start_utc: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))

    # Speed-trap readings (km/h) at the two intermediates, finish line and speed trap.
    speed_i1: Mapped[float | None] = mapped_column(Double)
    speed_i2: Mapped[float | None] = mapped_column(Double)
    speed_fl: Mapped[float | None] = mapped_column(Double)
    speed_st: Mapped[float | None] = mapped_column(Double)

    stint: Mapped[int | None] = mapped_column(SmallInteger)
    compound: Mapped[str | None] = mapped_column(Compound)
    tyre_life: Mapped[float | None] = mapped_column(Double)
    fresh_tyre: Mapped[bool | None] = mapped_column(Boolean)

    pit_in_s: Mapped[float | None] = mapped_column(Double)
    pit_out_s: Mapped[float | None] = mapped_column(Double)
    position: Mapped[float | None] = mapped_column(Double)

    # Raw concatenated status codes, e.g. '2671' means several statuses touched this lap.
    # Kept verbatim; decoded into booleans by the transform layer.
    track_status: Mapped[str | None] = mapped_column(String(16))

    is_personal_best: Mapped[bool | None] = mapped_column(Boolean)
    deleted: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    deleted_reason: Mapped[str | None] = mapped_column(Text)
    # FastF1's own quality flag: timing data for this lap is complete and self-consistent.
    is_accurate: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))


class Stint(Base):
    """A run on one set of tyres, derived from laps during transform."""

    __tablename__ = "stints"
    __table_args__ = (
        UniqueConstraint("session_id", "driver_number", "stint", name="uq_stints_key"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    session_id: Mapped[int] = mapped_column(
        ForeignKey("core.sessions.id", ondelete="CASCADE"), index=True
    )
    driver_number: Mapped[str] = mapped_column(String(3))
    stint: Mapped[int] = mapped_column(SmallInteger)

    compound: Mapped[str | None] = mapped_column(Compound)
    start_lap: Mapped[int | None] = mapped_column(SmallInteger)
    end_lap: Mapped[int | None] = mapped_column(SmallInteger)
    n_laps: Mapped[int | None] = mapped_column(SmallInteger)
    tyre_age_start: Mapped[float | None] = mapped_column(Double)
    fresh_tyre: Mapped[bool | None] = mapped_column(Boolean)


class PitStop(Base):
    __tablename__ = "pit_stops"
    __table_args__ = (
        UniqueConstraint("session_id", "driver_number", "stop_number", name="uq_pitstops_key"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    session_id: Mapped[int] = mapped_column(
        ForeignKey("core.sessions.id", ondelete="CASCADE"), index=True
    )
    driver_number: Mapped[str] = mapped_column(String(3))
    stop_number: Mapped[int] = mapped_column(SmallInteger)

    lap_number: Mapped[int | None] = mapped_column(SmallInteger)
    pit_in_s: Mapped[float | None] = mapped_column(Double)
    pit_out_s: Mapped[float | None] = mapped_column(Double)
    # In-lap pit entry to out-lap pit exit. Pit-lane travel, not just stationary time.
    pit_duration_s: Mapped[float | None] = mapped_column(Double)
    compound_in: Mapped[str | None] = mapped_column(Compound)
    compound_out: Mapped[str | None] = mapped_column(Compound)
