"""Stint and pit-stop derivation from lap data.

FastF1 supplies a per-lap ``stint`` number, but not the stint-level facts the
degradation engine needs: when each run started and ended, how long it was, and what
tyre age it began on. Nor does it give pit-stop durations — those have to be
reconstructed by pairing an in-lap's pit entry with the following out-lap's exit.
"""

from __future__ import annotations

import polars as pl

# Required by both derivations; named once so a schema change fails loudly in one place.
LAP_KEYS = ("session_id", "driver_number", "lap_number")


def derive_stints(laps: pl.DataFrame) -> pl.DataFrame:
    """Collapse laps into one row per driver stint.

    Tyre age at the start of a stint matters more than stint length alone: a driver
    switching to a used set carries degradation the lap count alone would not reveal.
    """
    if laps.is_empty():
        return pl.DataFrame(
            schema={
                "session_id": pl.Int32,
                "driver_number": pl.Utf8,
                "stint": pl.Int16,
                "compound": pl.Utf8,
                "start_lap": pl.Int16,
                "end_lap": pl.Int16,
                "n_laps": pl.Int16,
                "tyre_age_start": pl.Float64,
                "fresh_tyre": pl.Boolean,
            }
        )

    return (
        laps.filter(pl.col("stint").is_not_null())
        .group_by(["session_id", "driver_number", "stint"])
        .agg(
            # Compound is constant within a stint by definition; take the first
            # non-null rather than assuming every lap carries it.
            compound=pl.col("compound").drop_nulls().first(),
            start_lap=pl.col("lap_number").min(),
            end_lap=pl.col("lap_number").max(),
            n_laps=pl.len(),
            # Tyre life on the stint's first lap, which is the age the set already
            # carried when it went on the car.
            tyre_age_start=pl.col("tyre_life").sort_by("lap_number").drop_nulls().first(),
            fresh_tyre=pl.col("fresh_tyre").drop_nulls().first(),
        )
        .sort(["session_id", "driver_number", "stint"])
    )


def derive_pit_stops(laps: pl.DataFrame) -> pl.DataFrame:
    """Reconstruct pit stops by pairing each in-lap with the following out-lap.

    ``pit_duration_s`` spans pit entry to pit exit, so it includes pit-lane travel
    as well as stationary time. That is the quantity strategy cares about — the time
    actually surrendered to the track — not the stationary time quoted on broadcast.
    """
    empty_schema = {
        "session_id": pl.Int32,
        "driver_number": pl.Utf8,
        "stop_number": pl.Int16,
        "lap_number": pl.Int16,
        "pit_in_s": pl.Float64,
        "pit_out_s": pl.Float64,
        "pit_duration_s": pl.Float64,
        "compound_in": pl.Utf8,
        "compound_out": pl.Utf8,
    }
    if laps.is_empty():
        return pl.DataFrame(schema=empty_schema)

    ordered = laps.sort(["session_id", "driver_number", "lap_number"])

    # An in-lap carries pit_in_s; the stop completes on the next lap, which carries
    # pit_out_s. Pairing them across the lap boundary gives the full pit-lane loss.
    paired = ordered.with_columns(
        next_pit_out=pl.col("pit_out_s").shift(-1).over(["session_id", "driver_number"]),
        next_compound=pl.col("compound").shift(-1).over(["session_id", "driver_number"]),
    ).filter(pl.col("pit_in_s").is_not_null())

    if paired.is_empty():
        return pl.DataFrame(schema=empty_schema)

    return (
        paired.with_columns(
            stop_number=pl.col("lap_number")
            .rank("ordinal")
            .over(["session_id", "driver_number"])
            .cast(pl.Int16),
            pit_duration_s=pl.when(pl.col("next_pit_out").is_not_null())
            .then(pl.col("next_pit_out") - pl.col("pit_in_s"))
            .otherwise(None),
        )
        .select(
            "session_id",
            "driver_number",
            "stop_number",
            "lap_number",
            "pit_in_s",
            pit_out_s=pl.col("next_pit_out"),
            pit_duration_s=pl.col("pit_duration_s"),
            compound_in=pl.col("compound"),
            compound_out=pl.col("next_compound"),
        )
        .sort(["session_id", "driver_number", "stop_number"])
    )
