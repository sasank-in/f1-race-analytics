"""The session transform: core laps in, mart.lap_metrics out.

This module is the only place that knows the order the transforms must run in.
Everything it calls is pure, so the whole pipeline can be exercised on a frame
built in a test without touching a database.
"""

from __future__ import annotations

from dataclasses import dataclass

import polars as pl

from f1x.config import ENGINE_VERSION
from f1x.transform import corrections, stints, validity

# Columns mart.lap_metrics accepts, in the order the loader writes them.
METRIC_COLUMNS = (
    "session_id",
    "driver_number",
    "lap_number",
    "lap_time_s",
    "is_green",
    "is_in_lap",
    "is_out_lap",
    "is_representative",
    "exclusion_reason",
    "fuel_corrected_s",
    "fuel_load_kg",
    "evolution_corrected_s",
    "gap_ahead_s",
    "gap_behind_s",
    "is_clean_air",
    "stint",
    "compound",
    "tyre_life",
    "position",
)


@dataclass(frozen=True)
class TransformResult:
    """Everything one session's transform produces, plus how it got there."""

    lap_metrics: pl.DataFrame
    stints: pl.DataFrame
    pit_stops: pl.DataFrame
    exclusions: dict[str, int]
    engine_version: str = ENGINE_VERSION

    @property
    def representative_laps(self) -> int:
        if self.lap_metrics.is_empty():
            return 0
        return int(self.lap_metrics.get_column("is_representative").sum() or 0)


def transform_session(
    laps: pl.DataFrame,
    *,
    total_laps: int | None = None,
    fuel_effect: float | None = None,
) -> TransformResult:
    """Run the full per-session transform.

    Order matters. Validity is classified first so later steps can see which laps
    are trustworthy; fuel correction and traffic state are then applied to every lap
    regardless, because a lap excluded from the pace sample is still worth reporting
    with its correction attached when someone asks why it was excluded.
    """
    classified = validity.classify(laps)
    # A circuit-specific coefficient when one has been fitted across seasons,
    # otherwise the published default. See engine/pace/fuel_fit.py.
    fuelled = corrections.add_fuel_correction(
        classified,
        total_laps=total_laps,
        effect=fuel_effect if fuel_effect is not None else corrections.FUEL_EFFECT_S_PER_KG,
    )
    # Track evolution after fuel: the grip trend is measured on fuel-corrected times,
    # so removing fuel first is a prerequisite.
    evolved = corrections.add_evolution_correction(fuelled)
    with_traffic = corrections.add_traffic_state(evolved)

    return TransformResult(
        lap_metrics=_project(with_traffic),
        stints=stints.derive_stints(laps),
        pit_stops=stints.derive_pit_stops(laps),
        exclusions=validity.summarise(classified),
    )


def _project(frame: pl.DataFrame) -> pl.DataFrame:
    """Select the mart columns, filling any the source frame did not carry."""
    if frame.is_empty():
        return pl.DataFrame(schema={name: pl.Null for name in METRIC_COLUMNS})
    missing = [
        pl.lit(None).alias(name) for name in METRIC_COLUMNS if name not in frame.columns
    ]
    return frame.with_columns(missing).select(METRIC_COLUMNS)
