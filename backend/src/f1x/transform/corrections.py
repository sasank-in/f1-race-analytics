"""Fuel-load and traffic corrections.

A raw lap time is not pace. Two effects dominate and neither is visible in the source
data, so both must be modelled before any two laps are compared:

**Fuel.** A car starts a grand prix around 100 kg heavier than it finishes. That mass
costs roughly 0.03 s per kg per lap, so an opening lap is some three seconds slower
than an identical lap at the end on the same tyres. Without correcting for it, every
stint looks like it degrades and every late stint looks quick.

**Traffic.** A lap spent within a second of the car ahead is a lap spent in dirty air,
worth several tenths. Including those laps in a pace sample measures a driver's luck
in traffic rather than their car's speed.

Both corrections are estimates, not measurements. Fuel load is inferred from race
distance, and the coefficients below are published rules of thumb rather than team
data — good enough to rank pace, not precise enough to quote to the millisecond.
"""

from __future__ import annotations

import numpy as np
import polars as pl
from polars.datatypes import DataTypeClass

# Polars dtypes are classes (pl.Float64), not instances, so annotations use the class.
PolarsDataType = DataTypeClass

# Time cost of carrying one kilogram of fuel for one lap, in seconds. Varies with
# circuit — a long lap with heavy braking costs more than a short flowing one — but
# 0.030 is the usual working figure and is applied uniformly until per-circuit
# coefficients are fitted from data in a later phase.
FUEL_EFFECT_S_PER_KG: float = 0.030

# Provenance, carried explicitly so no downstream reader can mistake this for a value
# learned from the data. Three attempts to fit it from race timing all failed for the
# same reason — fuel_load_kg is constructed from lap number, so it carries no
# information lap number does not already have. See engine/pace/fuel_fit.py.
FUEL_EFFECT_SOURCE: str = "published_default"
FUEL_EFFECT_FITTED: bool = False

# Regulation maximum race fuel load. Teams start under this, but it sets the scale.
RACE_FUEL_KG: float = 100.0

# A car within this gap is in disturbed air and loses meaningful downforce.
CLEAN_AIR_GAP_S: float = 1.5


def _add_null_columns(
    frame: pl.DataFrame, columns: tuple[tuple[str, PolarsDataType], ...]
) -> pl.DataFrame:
    """Extend the schema with null columns, preserving an empty frame's emptiness.

    ``with_columns(pl.lit(None))`` on a zero-row frame produces a single all-null row,
    which downstream reads as a phantom lap. Appending empty Series keeps the shape.
    """
    if frame.is_empty():
        return frame.with_columns([pl.Series(name, [], dtype=dt) for name, dt in columns])
    return frame.with_columns([pl.lit(None, dtype=dt).alias(name) for name, dt in columns])


def add_fuel_correction(
    laps: pl.DataFrame,
    *,
    total_laps: int | None = None,
    start_fuel_kg: float = RACE_FUEL_KG,
    effect: float = FUEL_EFFECT_S_PER_KG,
) -> pl.DataFrame:
    """Add ``fuel_load_kg`` and ``fuel_corrected_s``.

    Corrects every lap to an empty-tank equivalent, so a lap-3 time and a lap-50 time
    become directly comparable. Assumes a linear burn across the scheduled distance,
    which is close enough on a green race and wrong under a long safety car — the
    engine consumes far less while circulating slowly. Neutralised laps are excluded
    from pace samples anyway, which limits the damage.

    With no ``total_laps`` (practice and qualifying, where fuel loads are unknown and
    deliberately varied) the correction is skipped and the columns are null. Inventing
    a fuel load for a practice run would fabricate precision that does not exist.
    """
    if laps.is_empty() or not total_laps or total_laps <= 0:
        return _add_null_columns(
            laps, (("fuel_load_kg", pl.Float64), ("fuel_corrected_s", pl.Float64))
        )

    burn_per_lap = start_fuel_kg / total_laps
    # Fuel remaining at the start of the lap: full on lap 1, empty at the flag.
    fuel = (start_fuel_kg - (pl.col("lap_number") - 1) * burn_per_lap).clip(0.0, start_fuel_kg)

    return laps.with_columns(fuel_load_kg=fuel).with_columns(
        fuel_corrected_s=pl.col("lap_time_s") - pl.col("fuel_load_kg") * effect
    )


def add_traffic_state(
    laps: pl.DataFrame, *, gap_threshold_s: float = CLEAN_AIR_GAP_S
) -> pl.DataFrame:
    """Add ``gap_ahead_s``, ``gap_behind_s`` and ``is_clean_air``.

    Gaps are derived from track position at the end of each lap: cars are ordered by
    position within a lap, and the gap is the difference in cumulative race time
    between adjacent cars. This is an approximation — it measures the gap at the line,
    not through the lap — but it is the only signal available without telemetry.

    A lap is clean-air when nothing is close enough ahead to disturb it. The car
    behind does not slow the car in front, so ``gap_behind_s`` is recorded for
    context but does not affect the flag.
    """
    required = {"lap_number", "position", "lap_time_s"}
    if laps.is_empty() or not required <= set(laps.columns):
        return _add_null_columns(
            laps,
            (
                ("gap_ahead_s", pl.Float64),
                ("gap_behind_s", pl.Float64),
                ("is_clean_air", pl.Boolean),
            ),
        )

    # Cumulative elapsed time per driver approximates race time at the line.
    with_elapsed = laps.sort(["session_id", "driver_number", "lap_number"]).with_columns(
        elapsed_s=pl.col("lap_time_s")
        .fill_null(0.0)
        .cum_sum()
        .over(["session_id", "driver_number"])
    )

    # Within each lap, order by position and diff against the car ahead.
    ordered = with_elapsed.sort(["session_id", "lap_number", "position"])
    return (
        ordered.with_columns(
            gap_ahead_s=(
                pl.col("elapsed_s")
                - pl.col("elapsed_s").shift(1).over(["session_id", "lap_number"])
            ),
            gap_behind_s=(
                pl.col("elapsed_s").shift(-1).over(["session_id", "lap_number"])
                - pl.col("elapsed_s")
            ),
        )
        .with_columns(
            # The leader has no car ahead, so a null gap means clean air, not unknown.
            is_clean_air=pl.when(pl.col("position") == 1)
            .then(pl.lit(True))
            .when(pl.col("gap_ahead_s").is_null())
            .then(pl.lit(None, dtype=pl.Boolean))
            .otherwise(pl.col("gap_ahead_s") > gap_threshold_s)
        )
        .sort(["session_id", "driver_number", "lap_number"])
    )


# Track evolution: rubber laid into the surface improves grip as a race goes on.
# Measured at Jeddah 2023, a driver's fuel-corrected times fall from 94.7 s to 92.6 s
# across eight laps of a single stint — a gain that exceeds tyre wear entirely.
#
# This is why several low-degradation circuits fit *negative* degradation slopes. It is
# not an artefact of the fuel correction: lowering the fuel coefficient makes those
# slopes more negative, not less, because the residual improvement is real.
#
# Removing it matters because the degradation model attributes everything left in a
# stint's slope to the tyre. With evolution still in the residual, a circuit that rubbers
# in quickly looks like it has tyres that improve with age.


def estimate_evolution(laps: pl.DataFrame, *, time_column: str = "fuel_corrected_s") -> float:
    """Seconds per lap the track improves, estimated across the whole session.

    Fitted on the *field*, not per driver: every car benefits from the same surface, so
    a session-wide trend separates evolution from any individual's tyre behaviour.

    Deliberately one number for the session. Evolution is strongest early and flattens
    once the racing line is rubbered in, but fitting that curve needs more signal than
    a single race provides, and a wrong curve is worse than a right line.
    """
    required = {"lap_number", time_column}
    if laps.is_empty() or not required <= set(laps.columns):
        return 0.0

    usable = laps.filter(pl.col("is_representative") & pl.col(time_column).is_not_null())
    if len(usable) < 50:
        return 0.0

    # Median per lap number, so one driver's traffic cannot tilt the trend.
    per_lap = (
        usable.group_by("lap_number")
        .agg(median_time=pl.col(time_column).median())
        .sort("lap_number")
    )
    if len(per_lap) < 10:
        return 0.0

    lap = per_lap.get_column("lap_number").to_numpy().astype(float)
    times = per_lap.get_column("median_time").to_numpy().astype(float)
    slope = float(np.polyfit(lap, times, 1)[0])

    # Only a *negative* slope is evolution. A positive session-wide trend means the
    # field is degrading faster than the track improves, which belongs to the tyre
    # model, not here.
    return min(0.0, slope)


def add_evolution_correction(
    laps: pl.DataFrame, *, time_column: str = "fuel_corrected_s"
) -> pl.DataFrame:
    """Add ``evolution_corrected_s``: fuel-corrected time with the grip trend removed.

    After this, a stint's remaining slope is attributable to the tyre rather than to
    the track surface improving underneath it.
    """
    if laps.is_empty() or time_column not in laps.columns:
        return _add_null_columns(laps, (("evolution_corrected_s", pl.Float64),))

    slope = estimate_evolution(laps, time_column=time_column)
    if slope == 0.0:
        return laps.with_columns(evolution_corrected_s=pl.col(time_column))

    # Subtract the improvement the track has already handed over by this lap.
    return laps.with_columns(
        evolution_corrected_s=pl.col(time_column) - slope * (pl.col("lap_number") - 1)
    )
