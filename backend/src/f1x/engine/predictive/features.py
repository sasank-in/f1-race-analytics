"""Feature store for outcome prediction.

Every feature here has to satisfy one rule: it must have been knowable *before* the
race it predicts. That sounds obvious and is the easiest thing in the world to get
wrong — a model fed a driver's average finishing position across the whole season will
predict that season's races almost perfectly, and predict nothing at all about a race
it has not seen.

So features are built strictly from a driver's *prior* races. A driver's first race of
a season has no history and is excluded rather than filled with a season-wide average,
which would leak the future into the past.

The features are deliberately few. With 598 race results across two seasons there is
not enough data to support a wide model, and a narrow one whose inputs are all
defensible is worth more than a broad one that memorises.
"""

from __future__ import annotations

from dataclasses import dataclass

import polars as pl

# A driver needs some history before their form means anything.
MIN_PRIOR_RACES = 2

# How many recent races define current form. Short enough to track a mid-season
# upgrade, long enough not to be one bad afternoon.
FORM_WINDOW = 5


@dataclass(frozen=True)
class FeatureSet:
    """Assembled features and the target they predict."""

    frame: pl.DataFrame
    feature_columns: tuple[str, ...]
    target_column: str

    @property
    def n_samples(self) -> int:
        return len(self.frame)

    def split_by_season(self, holdout_season: int) -> tuple[pl.DataFrame, pl.DataFrame]:
        """Split into train and test on a season boundary.

        A random split would let a model learn from a race and be tested on the one
        before it, which flatters the score. Holding out a whole season is the only
        split that answers the question actually being asked: given what we knew,
        would this have predicted the races that followed?
        """
        train = self.frame.filter(pl.col("season_year") != holdout_season)
        test = self.frame.filter(pl.col("season_year") == holdout_season)
        return train, test


FEATURE_COLUMNS: tuple[str, ...] = (
    "grid_position",
    "prior_mean_finish",
    "prior_best_finish",
    "prior_mean_pace_gap",
    "prior_finish_rate",
    "prior_races",
)


def build_features(results: pl.DataFrame, *, target: str = "position") -> FeatureSet:
    """Assemble per-driver, per-race features from prior results only.

    ``results`` must carry one row per driver per race with the season, round, grid
    and finishing position, plus the pace gap measured by the engine for that race.
    """
    required = {
        "season_year", "round", "driver_number", "grid_position", "position",
        "pace_gap_s",
    }
    if results.is_empty() or not required <= set(results.columns):
        return FeatureSet(pl.DataFrame(), FEATURE_COLUMNS, target)

    ordered = results.sort(["driver_number", "season_year", "round"])

    # Every aggregate is shifted by one race, so a row never sees its own result.
    # This shift is the whole defence against leakage.
    windowed = ordered.with_columns(
        prior_mean_finish=pl.col("position")
        .shift(1)
        .rolling_mean(window_size=FORM_WINDOW, min_samples=1)
        .over("driver_number"),
        prior_best_finish=pl.col("position")
        .shift(1)
        .rolling_min(window_size=FORM_WINDOW, min_samples=1)
        .over("driver_number"),
        prior_mean_pace_gap=pl.col("pace_gap_s")
        .shift(1)
        .rolling_mean(window_size=FORM_WINDOW, min_samples=1)
        .over("driver_number"),
        # Share of prior races finished, as a stand-in for reliability.
        prior_finish_rate=pl.col("position")
        .is_not_null()
        .cast(pl.Float64)
        .shift(1)
        .rolling_mean(window_size=FORM_WINDOW, min_samples=1)
        .over("driver_number"),
        prior_races=pl.col("position").cum_count().shift(1).over("driver_number"),
    )

    usable = windowed.filter(
        (pl.col("prior_races") >= MIN_PRIOR_RACES)
        & pl.col("grid_position").is_not_null()
        & pl.col(target).is_not_null()
        & pl.col("prior_mean_finish").is_not_null()
    )

    return FeatureSet(usable, FEATURE_COLUMNS, target)


def leakage_check(features: FeatureSet) -> list[str]:
    """Look for features that could not have been known before the race.

    Returns a list of problems, empty when the set is clean. Cheap to run and worth
    running: a leak inflates every score downstream and is invisible in the metrics.
    """
    problems: list[str] = []
    if features.frame.is_empty():
        return ["feature set is empty"]

    columns = set(features.frame.columns)
    for name in features.feature_columns:
        if name not in columns:
            problems.append(f"missing feature column: {name}")

    # The target must not appear among the inputs under another name.
    if features.target_column in features.feature_columns:
        problems.append(f"target {features.target_column} is also a feature")

    # A feature perfectly correlated with the target is the signature of a leak.
    target = features.frame.get_column(features.target_column)
    for name in features.feature_columns:
        if name not in columns:
            continue
        column = features.frame.get_column(name)
        if column.dtype in (pl.Float64, pl.Float32, pl.Int64, pl.Int32, pl.Int16):
            paired = features.frame.select([name, features.target_column]).drop_nulls()
            if len(paired) > 10:
                correlation = paired.select(
                    pl.corr(name, features.target_column)
                ).item()
                if correlation is not None and abs(correlation) > 0.98:
                    problems.append(
                        f"{name} correlates {correlation:.3f} with the target — "
                        "almost certainly leaked"
                    )
    del target
    return problems
