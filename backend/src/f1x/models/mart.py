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

    engine_version: Mapped[str] = mapped_column(String(16))
    computed_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )
