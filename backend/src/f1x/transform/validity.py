"""Lap validity classification.

Deciding which laps may enter a pace sample is the single most consequential step in
the pipeline: every downstream metric — stint regression, degradation slope, clean-air
ranking — inherits whatever bias this filter leaves behind.

The rules below are deliberately conservative. A lap is excluded when there is any
concrete reason to doubt it represents competitive pace, because a contaminated sample
produces a confident wrong answer, whereas a smaller sample merely produces a wider
confidence interval.
"""

from __future__ import annotations

from enum import StrEnum

import polars as pl

from f1x.transform import track_status


class Exclusion(StrEnum):
    """Why a lap was kept out of the pace sample. Stored, not just counted."""

    NONE = "none"
    NO_TIME = "no_time"
    DELETED = "deleted"
    INACCURATE = "inaccurate"
    NEUTRALISED = "neutralised"
    YELLOW = "yellow"
    IN_LAP = "in_lap"
    OUT_LAP = "out_lap"
    OUTLIER = "outlier"


# A lap this far above the session's best is not a representative racing lap: the
# driver lifted, was held up, or had a problem. Chosen loosely enough to keep genuine
# heavy-fuel and wet-weather laps, which can legitimately sit well off the ultimate pace.
OUTLIER_RATIO = 1.10


def classify(laps: pl.DataFrame) -> pl.DataFrame:
    """Add validity columns to a lap frame.

    Expects the columns produced by the ingestion layer and returns the same frame
    with ``is_green``, ``is_in_lap``, ``is_out_lap``, ``is_representative`` and
    ``exclusion_reason`` appended. Pure: no I/O, no mutation of the input.
    """
    if laps.is_empty():
        # with_columns on a zero-row frame yields a single all-null row, which would
        # inject a phantom lap. Extend the schema instead, keeping the frame empty.
        return laps.with_columns(
            [
                pl.Series(name, [], dtype=dtype)
                for name, dtype in (
                    ("is_green", pl.Boolean),
                    ("is_in_lap", pl.Boolean),
                    ("is_out_lap", pl.Boolean),
                    ("is_representative", pl.Boolean),
                    ("exclusion_reason", pl.Utf8),
                )
            ]
        )

    out = laps.with_columns(
        is_green=pl.col("track_status").map_elements(
            track_status.is_green, return_dtype=pl.Boolean
        ),
        is_neutralised=pl.col("track_status").map_elements(
            track_status.is_neutralised, return_dtype=pl.Boolean
        ),
        has_yellow=pl.col("track_status").map_elements(
            track_status.has_yellow, return_dtype=pl.Boolean
        ),
        # A lap with a pit-in time ends in the pit lane; one with a pit-out time
        # starts there. Both carry several seconds of pit-lane travel and neither
        # reflects the car's pace on track.
        is_in_lap=pl.col("pit_in_s").is_not_null(),
        is_out_lap=pl.col("pit_out_s").is_not_null(),
    )

    # Reference pace for outlier detection: the fastest green lap in the session.
    # Falls back to the fastest lap overall when nothing ran green, so a fully
    # neutralised session still gets a sane threshold rather than a null one.
    green_best = out.filter(pl.col("is_green") & pl.col("lap_time_s").is_not_null())
    reference = (
        green_best.get_column("lap_time_s").min()
        if not green_best.is_empty()
        else out.get_column("lap_time_s").min()
    )
    cutoff = float(reference) * OUTLIER_RATIO if reference is not None else None  # type: ignore[arg-type]

    # Ordered: the first matching condition is the reason recorded, so the most
    # fundamental problem wins over a secondary one.
    reason: pl.Expr = (
        pl.when(pl.col("lap_time_s").is_null())
        .then(pl.lit(Exclusion.NO_TIME.value))
        .when(pl.col("deleted"))
        .then(pl.lit(Exclusion.DELETED.value))
        .when(~pl.col("is_accurate"))
        .then(pl.lit(Exclusion.INACCURATE.value))
        .when(pl.col("is_neutralised"))
        .then(pl.lit(Exclusion.NEUTRALISED.value))
        .when(pl.col("is_in_lap"))
        .then(pl.lit(Exclusion.IN_LAP.value))
        .when(pl.col("is_out_lap"))
        .then(pl.lit(Exclusion.OUT_LAP.value))
        .when(pl.col("has_yellow"))
        .then(pl.lit(Exclusion.YELLOW.value))
    )
    if cutoff is not None:
        reason = reason.when(pl.col("lap_time_s") > cutoff).then(  # type: ignore[attr-defined]
            pl.lit(Exclusion.OUTLIER.value)
        )
    reason = reason.otherwise(pl.lit(Exclusion.NONE.value))  # type: ignore[attr-defined]

    return out.with_columns(exclusion_reason=reason).with_columns(
        is_representative=pl.col("exclusion_reason") == Exclusion.NONE.value
    )


def summarise(classified: pl.DataFrame) -> dict[str, int]:
    """Count laps by exclusion reason — the first thing to check when pace looks wrong."""
    if classified.is_empty():
        return {}
    counts = (
        classified.group_by("exclusion_reason")
        .agg(pl.len().alias("n"))
        .sort("n", descending=True)
    )
    return dict(zip(counts["exclusion_reason"], counts["n"], strict=True))
