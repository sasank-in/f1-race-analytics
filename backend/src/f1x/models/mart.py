"""Engine output (schema `mart`).

Every table here is derived and disposable: it can be dropped and recomputed from `core`.
Each row carries the `engine_version` that produced it so a model change invalidates
stale rows instead of silently mixing definitions.

Phase 1 defines only `lap_metrics`, the per-lap fact table the pace and degradation
engines consume. Later phases add deg_models, strategy_windows and ratings.
"""

from __future__ import annotations

import datetime as dt

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    Double,
    ForeignKey,
    Index,
    Integer,
    SmallInteger,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from f1x.models.base import Base


class LapMetric(Base):
    """Per-lap derived facts: validity, fuel correction, traffic state.

    Grain matches `core.laps` one-to-one, but only for laps that survive validity
    filtering — an unrepresentative lap has no meaningful corrected pace.
    """

    __tablename__ = "lap_metrics"
    __table_args__ = (
        UniqueConstraint(
            "session_id", "driver_number", "lap_number", "engine_version",
            name="uq_lap_metrics_key",
        ),
        Index("ix_lap_metrics_session", "session_id", "engine_version"),
        Index(
            "ix_lap_metrics_representative",
            "session_id", "driver_number",
            postgresql_where=text("is_representative"),
        ),
        {"schema": "mart", "comment": "Derived per-lap facts; recomputable from core"},
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    session_id: Mapped[int] = mapped_column(ForeignKey("core.sessions.id", ondelete="CASCADE"))
    driver_number: Mapped[str] = mapped_column(String(3))
    lap_number: Mapped[int] = mapped_column(SmallInteger)

    lap_time_s: Mapped[float | None] = mapped_column(Double)

    # --- validity ---------------------------------------------------------
    is_green: Mapped[bool | None] = mapped_column(Boolean)      # no SC/VSC/yellow
    is_in_lap: Mapped[bool | None] = mapped_column(Boolean)
    is_out_lap: Mapped[bool | None] = mapped_column(Boolean)
    # Survives every filter and belongs in a pace sample.
    is_representative: Mapped[bool | None] = mapped_column(Boolean)
    exclusion_reason: Mapped[str | None] = mapped_column(String(32))

    # --- corrections ------------------------------------------------------
    # Lap time adjusted to a constant fuel load, so laps from different race phases
    # are comparable. Correction assumes a linear burn over the race distance.
    fuel_corrected_s: Mapped[float | None] = mapped_column(Double)
    fuel_load_kg: Mapped[float | None] = mapped_column(Double)
    # Session-wide grip trend removed.
    evolution_corrected_s: Mapped[float | None] = mapped_column(Double)

    # --- traffic ----------------------------------------------------------
    gap_ahead_s: Mapped[float | None] = mapped_column(Double)
    gap_behind_s: Mapped[float | None] = mapped_column(Double)
    is_clean_air: Mapped[bool | None] = mapped_column(Boolean)

    stint: Mapped[int | None] = mapped_column(SmallInteger)
    compound: Mapped[str | None] = mapped_column(String(16))
    tyre_life: Mapped[float | None] = mapped_column(Double)
    # Track position at the end of the lap. Carried through from core.laps because
    # the undercut scanner needs it to know which car was ahead of which.
    position: Mapped[float | None] = mapped_column(Double)

    engine_version: Mapped[str] = mapped_column(String(16))
    computed_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )


class StintFitRow(Base):
    """One fitted stint: pace and degradation separated by regression."""

    __tablename__ = "stint_fits"
    __table_args__ = (
        UniqueConstraint(
            "session_id", "driver_number", "stint", "engine_version",
            name="uq_stint_fits_key",
        ),
        Index("ix_stint_fits_session", "session_id", "engine_version"),
        {"schema": "mart", "comment": "Per-stint pace/degradation split; recomputable"},
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    session_id: Mapped[int] = mapped_column(ForeignKey("core.sessions.id", ondelete="CASCADE"))
    driver_number: Mapped[str] = mapped_column(String(3))
    stint: Mapped[int] = mapped_column(SmallInteger)
    compound: Mapped[str | None] = mapped_column(String(16))
    n_laps: Mapped[int | None] = mapped_column(SmallInteger)

    # Lap time at zero tyre age: the stint's pace with degradation removed.
    pace_s: Mapped[float | None] = mapped_column(Double)
    degradation_s_per_lap: Mapped[float | None] = mapped_column(Double)
    r_squared: Mapped[float | None] = mapped_column(Double)
    residual_std_s: Mapped[float | None] = mapped_column(Double)
    tyre_age_start: Mapped[float | None] = mapped_column(Double)
    # False when the fit is too short, too noisy, or physically implausible.
    is_reliable: Mapped[bool | None] = mapped_column(Boolean)
    # False when the fitted slope is negative. Kept alongside the original value
    # rather than replacing it: a negative estimate is evidence the stint could not
    # support a reliable slope, and deleting it would hide that.
    is_physical: Mapped[bool | None] = mapped_column(Boolean)
    # Audit trail for the fit: how much tyre-age range it actually had to work with,
    # and how many laps the warm-up cutoff removed.
    tyre_age_range: Mapped[float | None] = mapped_column(Double)
    excluded_lap_count: Mapped[int | None] = mapped_column(SmallInteger)

    engine_version: Mapped[str] = mapped_column(String(16))
    computed_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )


class PaceRanking(Base):
    """Driver pace in one session, ranked on a quantile of clean corrected laps."""

    __tablename__ = "pace_rankings"
    __table_args__ = (
        UniqueConstraint(
            "session_id", "driver_number", "engine_version", name="uq_pace_rankings_key"
        ),
        Index("ix_pace_rankings_session", "session_id", "engine_version"),
        {"schema": "mart", "comment": "Per-driver session pace; recomputable"},
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    session_id: Mapped[int] = mapped_column(ForeignKey("core.sessions.id", ondelete="CASCADE"))
    driver_number: Mapped[str] = mapped_column(String(3))

    n_laps: Mapped[int | None] = mapped_column(SmallInteger)
    pace_s: Mapped[float | None] = mapped_column(Double)
    best_s: Mapped[float | None] = mapped_column(Double)
    median_s: Mapped[float | None] = mapped_column(Double)
    std_s: Mapped[float | None] = mapped_column(Double)
    clean_air_laps: Mapped[int | None] = mapped_column(SmallInteger)
    gap_to_best_s: Mapped[float | None] = mapped_column(Double)
    rank: Mapped[int | None] = mapped_column(SmallInteger)

    engine_version: Mapped[str] = mapped_column(String(16))
    computed_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )


class DegradationCurveRow(Base):
    """Pooled degradation for one compound at one circuit."""

    __tablename__ = "degradation_curves"
    __table_args__ = (
        UniqueConstraint(
            "session_id", "compound", "engine_version", name="uq_degradation_curves_key"
        ),
        {"schema": "mart", "comment": "Per-compound degradation; recomputable"},
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    session_id: Mapped[int] = mapped_column(ForeignKey("core.sessions.id", ondelete="CASCADE"))
    compound: Mapped[str] = mapped_column(String(16))

    n_stints: Mapped[int | None] = mapped_column(SmallInteger)
    n_laps: Mapped[int | None] = mapped_column(Integer)
    degradation_s_per_lap: Mapped[float | None] = mapped_column(Double)
    # Spread across stints — the honest width of the estimate, not just its centre.
    degradation_iqr_s: Mapped[float | None] = mapped_column(Double)
    median_pace_s: Mapped[float | None] = mapped_column(Double)
    max_stint_laps: Mapped[int | None] = mapped_column(SmallInteger)

    engine_version: Mapped[str] = mapped_column(String(16))
    computed_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )
