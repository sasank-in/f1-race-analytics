"""Driver pace ranking.

Ranking by fastest lap rewards whoever had the best single opportunity — a late soft
run on an empty track. Ranking by average lap time rewards whoever spent least time in
traffic. Neither measures the car.

This module ranks on the *quantile* of fuel-corrected, representative laps. Taking a
low quantile rather than the minimum keeps the ranking robust to one exceptional lap
while still describing what the car could do when the driver was pushing.
"""

from __future__ import annotations

from dataclasses import dataclass

import polars as pl

# Low enough to reflect genuine pace, high enough that a single outlier lap cannot
# define a driver's rating.
PACE_QUANTILE = 0.20

# A driver with fewer clean laps than this did not have a representative race —
# an early retirement, or a race spent entirely in traffic.
MIN_LAPS_FOR_RANKING = 10


@dataclass(frozen=True)
class DriverPace:
    """One driver's pace in one session."""

    session_id: int
    driver_number: str
    n_laps: int
    pace_s: float
    best_s: float
    median_s: float
    # Consistency: the spread of a driver's clean laps. A low value means they hit
    # the same time repeatedly, which matters as much as raw speed over a stint.
    std_s: float
    clean_air_laps: int

    gap_to_best_s: float = 0.0
    rank: int = 0


def rank_session(
    laps: pl.DataFrame,
    *,
    time_column: str = "fuel_corrected_s",
    quantile: float = PACE_QUANTILE,
) -> list[DriverPace]:
    """Rank every driver in one session by representative pace.

    Falls back to raw lap time when no fuel-corrected column exists, which is correct
    for qualifying — every car runs light, so there is no fuel effect to remove.
    """
    if laps.is_empty():
        return []

    column = time_column if time_column in laps.columns else "lap_time_s"
    if column not in laps.columns:
        return []

    usable = laps.filter(pl.col("is_representative") & pl.col(column).is_not_null())
    if usable.is_empty():
        return []

    aggregated = (
        usable.group_by(["session_id", "driver_number"])
        .agg(
            n_laps=pl.len(),
            pace_s=pl.col(column).quantile(quantile),
            best_s=pl.col(column).min(),
            median_s=pl.col(column).median(),
            std_s=pl.col(column).std(),
            clean_air_laps=pl.col("is_clean_air").sum()
            if "is_clean_air" in usable.columns
            else pl.lit(0),
        )
        .filter(pl.col("n_laps") >= MIN_LAPS_FOR_RANKING)
        .sort("pace_s")
    )
    if aggregated.is_empty():
        return []

    fastest = aggregated.get_column("pace_s").min()
    return [
        DriverPace(
            session_id=int(row["session_id"]),
            driver_number=str(row["driver_number"]),
            n_laps=int(row["n_laps"]),
            pace_s=float(row["pace_s"]),
            best_s=float(row["best_s"]),
            median_s=float(row["median_s"]),
            std_s=float(row["std_s"] or 0.0),
            clean_air_laps=int(row["clean_air_laps"] or 0),
            gap_to_best_s=float(row["pace_s"]) - float(fastest),  # type: ignore[arg-type]
            rank=position,
        )
        for position, row in enumerate(aggregated.to_dicts(), start=1)
    ]


def to_frame(ranking: list[DriverPace]) -> pl.DataFrame:
    """Collect a ranking into a frame."""
    if not ranking:
        return pl.DataFrame(
            schema={
                "session_id": pl.Int32,
                "driver_number": pl.Utf8,
                "n_laps": pl.Int16,
                "pace_s": pl.Float64,
                "best_s": pl.Float64,
                "median_s": pl.Float64,
                "std_s": pl.Float64,
                "clean_air_laps": pl.Int16,
                "gap_to_best_s": pl.Float64,
                "rank": pl.Int16,
            }
        )
    return pl.DataFrame([entry.__dict__ for entry in ranking])
