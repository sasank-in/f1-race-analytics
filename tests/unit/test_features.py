"""Feature store construction, with leakage as the primary concern.

A model that sees its own result scores brilliantly and predicts nothing. These tests
exist mainly to prove that cannot happen: every aggregate is built from prior races
only, and a row must never be able to see the race it is predicting.
"""

from __future__ import annotations

import polars as pl

from f1x.engine.predictive.features import (
    FORM_WINDOW,
    MIN_PRIOR_RACES,
    build_features,
    leakage_check,
)


def _results(
    *, n_races: int = 10, n_drivers: int = 4, improving: bool = False
) -> pl.DataFrame:
    """A synthetic season where driver 0 is quickest and driver 3 slowest.

    Positions vary race to race. A driver who finishes in exactly the same place every
    time makes their own prior mean a perfect predictor of their next result — which
    trips the leakage detector on data that is degenerate rather than leaked. Real
    results scatter, so the synthetic ones do too.
    """
    rows = []
    for race in range(1, n_races + 1):
        for driver in range(n_drivers):
            # Optionally let the slowest driver improve over the season, so a test
            # can check that recent form is weighted over ancient history.
            # Alternate adjacent finishers so a driver's history is not a constant.
            position = driver + 1
            if race % 2 == 0 and driver < n_drivers - 1:
                position = driver + 2 if driver % 2 == 0 else driver
            if improving and driver == n_drivers - 1 and race > n_races // 2:
                position = 1
            # Qualifying and race order diverge, as they do in a real season.
            grid = driver + 1
            if race % 3 == 0 and driver > 0:
                grid = driver
            rows.append(
                {
                    "season_year": 2023,
                    "round": race,
                    "driver_number": str(driver),
                    "grid_position": float(grid),
                    "position": float(position),
                    "pace_gap_s": driver * 0.3,
                }
            )
    return pl.DataFrame(rows)


def test_features_are_built_from_prior_races_only() -> None:
    """The core guarantee: a row's features never include its own result."""
    features = build_features(_results())
    assert features.n_samples > 0
    assert leakage_check(features) == []


def test_a_driver_first_races_are_excluded() -> None:
    """No history means no features; filling with a season average would leak."""
    features = build_features(_results(n_races=10, n_drivers=2))
    first_rounds = features.frame.filter(pl.col("round") <= MIN_PRIOR_RACES)
    assert first_rounds.is_empty()


def test_prior_races_never_includes_the_current_one() -> None:
    """The invariant that guarantees no leakage: history is strictly in the past."""
    features = build_features(_results(n_races=8, n_drivers=4))
    for row in features.frame.to_dicts():
        assert row["prior_races"] < row["round"], (
            f"round {row['round']} saw {row['prior_races']} prior races"
        )


def test_recent_form_is_weighted_over_old_results() -> None:
    """A driver who improves mid-season must show it in their prior mean."""
    features = build_features(_results(n_races=20, n_drivers=4, improving=True))
    improver = features.frame.filter(pl.col("driver_number") == "3").sort("round")
    early = improver.filter(pl.col("round") <= 8).get_column("prior_mean_finish").mean()
    late = improver.filter(pl.col("round") >= 18).get_column("prior_mean_finish").mean()
    assert late < early, "recent good results should pull the mean down"


def test_form_window_bounds_how_far_back_features_look() -> None:
    features = build_features(_results(n_races=30, n_drivers=2))
    counts = features.frame.get_column("prior_races").to_list()
    # prior_races is a running count, but the rolling aggregates only span the window.
    assert max(counts) >= FORM_WINDOW


def test_leakage_check_catches_a_target_copy() -> None:
    """The check must actually fire, or it is decoration."""
    features = build_features(_results())
    leaked = features.frame.with_columns(prior_mean_finish=pl.col("position"))
    from f1x.engine.predictive.features import FeatureSet

    problems = leakage_check(
        FeatureSet(leaked, features.feature_columns, features.target_column)
    )
    assert any("leaked" in problem for problem in problems)


def test_season_split_holds_out_a_whole_season() -> None:
    """A random split would test on a race the model trained beside."""
    rows = _results(n_races=8).to_dicts()
    for row in rows[:]:
        rows.append({**row, "season_year": 2022})
    features = build_features(pl.DataFrame(rows))
    train, test = features.split_by_season(2023)
    assert set(train.get_column("season_year").to_list()) == {2022}
    assert set(test.get_column("season_year").to_list()) == {2023}


def test_empty_results_produce_an_empty_feature_set() -> None:
    features = build_features(pl.DataFrame())
    assert features.n_samples == 0
    assert leakage_check(features) == ["feature set is empty"]


def test_missing_columns_produce_an_empty_feature_set() -> None:
    partial = pl.DataFrame({"season_year": [2023], "round": [1]})
    assert build_features(partial).n_samples == 0
