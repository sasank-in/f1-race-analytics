"""Composite driver ratings.

A championship table rewards the car as much as the driver. These ratings try to
separate the two by scoring four things the data can actually speak to:

**Pace** — how close to the fastest car, from the engine's own measured gap.
**Racecraft** — positions gained against what the grid slot predicted. A driver who
starts twelfth and finishes eighth did something the car did not do for them.
**Consistency** — the spread of their clean lap times. Repeatability wins races over
a stint even when single-lap pace does not.
**Tyre management** — degradation slope relative to the field on the same compound at
the same circuit, which is the only fair comparison available.

Each component is normalised to 0-100 across the drivers being compared, so a rating
is explicitly *relative*: it says who was better, never how good anyone was in
absolute terms. Comparing ratings across seasons is meaningless and the API does not
offer it.

The weights below are a judgement, not a finding. They are stated here rather than
buried so that anyone who disagrees can change them and see what moves.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import polars as pl

# How much each component contributes. Pace dominates because it is measured most
# directly; racecraft is weighted second because it is the clearest driver signal.
WEIGHTS: dict[str, float] = {
    "pace": 0.40,
    "racecraft": 0.25,
    "consistency": 0.20,
    "tyre_management": 0.15,
}

# A driver needs this many races before a rating means anything.
MIN_RACES = 3


@dataclass(frozen=True)
class DriverRating:
    """One driver's composite rating and the components behind it."""

    driver_number: str
    n_races: int

    pace: float
    racecraft: float
    consistency: float
    tyre_management: float

    overall: float
    rank: int = 0

    @property
    def strongest(self) -> str:
        """The component this driver scores highest on."""
        components = {
            "pace": self.pace,
            "racecraft": self.racecraft,
            "consistency": self.consistency,
            "tyre_management": self.tyre_management,
        }
        return max(components, key=lambda k: components[k])


def _normalise(values: np.ndarray, *, higher_is_better: bool) -> np.ndarray:
    """Scale to 0-100 across the drivers being compared.

    Min-max rather than a z-score: the output is a ranking aid, and a bounded scale
    is easier to read than standard deviations. A degenerate spread returns 50 for
    everyone rather than dividing by zero — no information means no separation.
    """
    finite = values[np.isfinite(values)]
    if finite.size == 0:
        return np.full(values.size, 50.0)
    low, high = float(finite.min()), float(finite.max())
    if high - low < 1e-9:
        return np.full(values.size, 50.0)
    scaled = (values - low) / (high - low) * 100.0
    return scaled if higher_is_better else 100.0 - scaled


def build_ratings(
    pace: pl.DataFrame,
    results: pl.DataFrame,
    stint_fits: pl.DataFrame,
    *,
    weights: dict[str, float] | None = None,
) -> list[DriverRating]:
    """Combine pace, results and stint fits into per-driver ratings.

    ``pace`` is mart.pace_rankings, ``results`` carries grid and finishing positions,
    and ``stint_fits`` is mart.stint_fits. Drivers missing from any of them are still
    rated, with the missing component scored at the midpoint rather than dropped —
    a driver who never had a valid stint fit should not be penalised as if they had
    the worst tyre management in the field.
    """
    weights = weights or WEIGHTS
    if pace.is_empty() or "driver_number" not in pace.columns:
        return []

    # --- pace and consistency, from the engine's own rankings ---
    aggregated = (
        pace.group_by("driver_number")
        .agg(
            n_races=pl.len(),
            mean_gap=pl.col("gap_to_best_s").mean(),
            mean_std=pl.col("std_s").mean(),
        )
        .filter(pl.col("n_races") >= MIN_RACES)
        .sort("driver_number")
    )
    if aggregated.is_empty():
        return []

    drivers = aggregated.get_column("driver_number").to_list()
    index = {driver: i for i, driver in enumerate(drivers)}

    gaps = aggregated.get_column("mean_gap").fill_null(strategy="mean").to_numpy()
    spreads = aggregated.get_column("mean_std").fill_null(strategy="mean").to_numpy()

    # --- racecraft: positions gained against the grid ---
    gained = np.zeros(len(drivers))
    if not results.is_empty() and {"grid_position", "position"} <= set(results.columns):
        racecraft = (
            results.filter(
                pl.col("grid_position").is_not_null() & pl.col("position").is_not_null()
            )
            .group_by("driver_number")
            .agg(gained=(pl.col("grid_position") - pl.col("position")).mean())
        )
        for row in racecraft.to_dicts():
            if row["driver_number"] in index:
                gained[index[row["driver_number"]]] = float(row["gained"] or 0.0)

    # --- tyre management: degradation relative to the field ---
    # Compared within compound and session, since an absolute slope mixes together
    # circuits and compounds that are not comparable.
    degradation = np.full(len(drivers), np.nan)
    if not stint_fits.is_empty() and "degradation_s_per_lap" in stint_fits.columns:
        reliable = stint_fits.filter(
            pl.col("is_reliable") & pl.col("degradation_s_per_lap").is_not_null()
        )
        if not reliable.is_empty():
            relative = reliable.with_columns(
                relative_deg=pl.col("degradation_s_per_lap")
                - pl.col("degradation_s_per_lap").mean().over(["session_id", "compound"])
            )
            per_driver = relative.group_by("driver_number").agg(
                mean_relative=pl.col("relative_deg").mean()
            )
            for row in per_driver.to_dicts():
                if row["driver_number"] in index:
                    degradation[index[row["driver_number"]]] = float(
                        row["mean_relative"] or 0.0
                    )
    # A driver with no usable stint fit sits at the field average, not the bottom.
    if np.isnan(degradation).all():
        degradation = np.zeros(len(drivers))
    else:
        degradation = np.nan_to_num(degradation, nan=float(np.nanmean(degradation)))

    pace_score = _normalise(gaps, higher_is_better=False)
    racecraft_score = _normalise(gained, higher_is_better=True)
    consistency_score = _normalise(spreads, higher_is_better=False)
    tyre_score = _normalise(degradation, higher_is_better=False)

    overall = (
        weights["pace"] * pace_score
        + weights["racecraft"] * racecraft_score
        + weights["consistency"] * consistency_score
        + weights["tyre_management"] * tyre_score
    )

    ratings = [
        DriverRating(
            driver_number=driver,
            n_races=int(aggregated.get_column("n_races")[i]),
            pace=float(pace_score[i]),
            racecraft=float(racecraft_score[i]),
            consistency=float(consistency_score[i]),
            tyre_management=float(tyre_score[i]),
            overall=float(overall[i]),
        )
        for i, driver in enumerate(drivers)
    ]

    ranked = sorted(ratings, key=lambda r: -r.overall)
    return [
        DriverRating(**{**rating.__dict__, "rank": position})
        for position, rating in enumerate(ranked, start=1)
    ]
