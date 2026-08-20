"""Hypertable models (schema `core`).

These four tables are converted to TimescaleDB hypertables by the migration; the ORM
definitions below only describe their columns and constraints.

Two constraints shape every design choice here:

1. Timescale requires the partitioning column to be part of any unique index, so the
   primary key is always composite and ends with the time column.
2. Volume. One grand prix produces ~721k car-telemetry rows and a similar number of
   position rows across 20 drivers, so a full season is ~175M rows. Columns are kept
   narrow (real over double, smallint over int) because width here costs gigabytes.
"""

from __future__ import annotations

import datetime as dt

from sqlalchemy import (
    REAL,
    Boolean,
    DateTime,
    Double,
    ForeignKey,
    Index,
    Integer,
    SmallInteger,
    String,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column

from f1x.models.base import Base


class Telemetry(Base):
    """Car data at ~4 Hz. The largest table in the system."""

    __tablename__ = "telemetry"
    __table_args__ = (
        Index("ix_telemetry_session_driver_ts", "session_id", "driver_number", "ts"),
        {"comment": "Hypertable. Grain: one row per driver per telemetry sample"},
    )

    # Absolute UTC, derived from session t0 + SessionTime, so samples from different
    # sessions never share a time coordinate.
    ts: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), primary_key=True)
    session_id: Mapped[int] = mapped_column(
        ForeignKey("core.sessions.id", ondelete="CASCADE"), primary_key=True
    )
    driver_number: Mapped[str] = mapped_column(String(3), primary_key=True)

    # Session-relative seconds, kept alongside ts for cheap lap-window joins.
    session_s: Mapped[float] = mapped_column(Double)

    speed: Mapped[float | None] = mapped_column(REAL)       # km/h
    rpm: Mapped[float | None] = mapped_column(REAL)
    gear: Mapped[int | None] = mapped_column(SmallInteger)
    throttle: Mapped[float | None] = mapped_column(REAL)    # 0-100 %
    brake: Mapped[bool | None] = mapped_column(Boolean)
    drs: Mapped[int | None] = mapped_column(SmallInteger)   # raw DRS state code

    # 'car' when reported by the feed, 'interpolated' when FastF1 filled the gap.
    source: Mapped[str | None] = mapped_column(String(16))


class Position(Base):
    """Track position at ~4 Hz. Drives corner detection and racing-line comparison."""

    __tablename__ = "positions"
    __table_args__ = (
        Index("ix_positions_session_driver_ts", "session_id", "driver_number", "ts"),
        {"comment": "Hypertable. Grain: one row per driver per position sample"},
    )

    ts: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), primary_key=True)
    session_id: Mapped[int] = mapped_column(
        ForeignKey("core.sessions.id", ondelete="CASCADE"), primary_key=True
    )
    driver_number: Mapped[str] = mapped_column(String(3), primary_key=True)

    session_s: Mapped[float] = mapped_column(Double)

    # Tenths of a metre in the source feed, stored as given. Real is ample: track
    # coordinates span ~1e4 and double would waste 4 bytes on ~87M rows per season.
    x: Mapped[float | None] = mapped_column(REAL)
    y: Mapped[float | None] = mapped_column(REAL)
    z: Mapped[float | None] = mapped_column(REAL)
    status: Mapped[str | None] = mapped_column(String(16))  # OnTrack / OffTrack


class Weather(Base):
    """Session weather, sampled per minute. Low volume but time-partitioned to match."""

    __tablename__ = "weather"
    __table_args__ = (
        Index("ix_weather_session_ts", "session_id", "ts"),
        {"comment": "Hypertable. Grain: one row per session per sample"},
    )

    ts: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), primary_key=True)
    session_id: Mapped[int] = mapped_column(
        ForeignKey("core.sessions.id", ondelete="CASCADE"), primary_key=True
    )

    session_s: Mapped[float] = mapped_column(Double)
    air_temp: Mapped[float | None] = mapped_column(REAL)
    track_temp: Mapped[float | None] = mapped_column(REAL)
    humidity: Mapped[float | None] = mapped_column(REAL)
    pressure: Mapped[float | None] = mapped_column(REAL)
    wind_speed: Mapped[float | None] = mapped_column(REAL)
    wind_direction: Mapped[int | None] = mapped_column(SmallInteger)
    rainfall: Mapped[bool | None] = mapped_column(Boolean)


class RaceControl(Base):
    """Race control messages: flags, safety cars, investigations, penalties."""

    __tablename__ = "race_control"
    __table_args__ = (
        Index("ix_race_control_session_ts", "session_id", "ts"),
        {"comment": "Hypertable. Grain: one row per message"},
    )

    ts: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), primary_key=True)
    session_id: Mapped[int] = mapped_column(
        ForeignKey("core.sessions.id", ondelete="CASCADE"), primary_key=True
    )
    # Several messages can share a timestamp, so the PK needs a tiebreaker.
    seq: Mapped[int] = mapped_column(Integer, primary_key=True)

    session_s: Mapped[float | None] = mapped_column(Double)
    category: Mapped[str | None] = mapped_column(String(32))
    message: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str | None] = mapped_column(String(32))
    flag: Mapped[str | None] = mapped_column(String(32))
    scope: Mapped[str | None] = mapped_column(String(32))
    sector: Mapped[int | None] = mapped_column(SmallInteger)
    driver_number: Mapped[str | None] = mapped_column(String(3))
    lap_number: Mapped[int | None] = mapped_column(SmallInteger)
