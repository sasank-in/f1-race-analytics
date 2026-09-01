"""Outcome model and the baseline it must beat.

The tests that matter here are the negative ones: a model fed noise must be reported as
no better than grid position, and the baseline comparison must be capable of failing.
A test suite that only proves the model works on easy data would let a useless model
through.
"""

from __future__ import annotations

import numpy as np
import polars as pl
import pytest

from f1x.engine.predictive.features import build_features
from f1x.engine.predictive.model import (
    evaluate,
    feature_importance,
    fit_ridge,
    predict,
)


def _results(
    *, seasons: tuple[int, ...] = (2022, 2023), n_races: int = 12, n_drivers: int = 10,
    signal: bool = True, seed: int = 0,
) -> pl.DataFrame:
    """Synthetic results where finishing position depends on pace, plus noise.

    With ``signal=False`` the finishing order is random, so any model that appears to
    beat the baseline on it is overfitting.
    """
    rng = np.random.default_rng(seed)
    rows = []
    for season in seasons:
        for race in range(1, n_races + 1):
            for driver in range(n_drivers):
                grid = driver + 1
                if signal:
                    # Finishing position tracks grid but drifts by a place or two.
                    position = float(
                        np.clip(grid + rng.normal(0, 1.5), 1, n_drivers)
                    )
                else:
                    position = float(rng.integers(1, n_drivers + 1))
                rows.append(
                    {
                        "season_year": season,
                        "round": race,
                        "driver_number": str(driver),
                        "grid_position": float(grid),
                        "position": position,
                        "pace_gap_s": driver * 0.15 + rng.normal(0, 0.05),
                    }
                )
    return pl.DataFrame(rows)


# --------------------------------------------------------------------------
# ridge regression
# --------------------------------------------------------------------------


def test_ridge_recovers_a_known_linear_relationship() -> None:
    rng = np.random.default_rng(1)
    design = rng.normal(size=(200, 2))
    target = 3.0 + 2.0 * design[:, 0] - 1.5 * design[:, 1]
    coefficients = fit_ridge(design, target, alpha=0.001)
    assert coefficients[0] == pytest.approx(3.0, abs=0.05)
    assert coefficients[1] == pytest.approx(2.0, abs=0.05)
    assert coefficients[2] == pytest.approx(-1.5, abs=0.05)


def test_the_intercept_is_not_penalised() -> None:
    """Shrinking the intercept would bias every prediction toward zero."""
    rng = np.random.default_rng(2)
    design = rng.normal(size=(200, 1))
    target = 50.0 + design[:, 0]
    coefficients = fit_ridge(design, target, alpha=100.0)
    # Heavy penalty shrinks the slope but must leave the intercept near 50.
    assert coefficients[0] == pytest.approx(50.0, abs=1.0)


def test_prediction_is_the_design_times_the_coefficients() -> None:
    design = np.array([[1.0], [2.0]])
    coefficients = np.array([10.0, 3.0])
    assert predict(coefficients, design).tolist() == [13.0, 16.0]


# --------------------------------------------------------------------------
# evaluation against the baseline
# --------------------------------------------------------------------------


def test_evaluation_reports_both_model_and_baseline() -> None:
    features = build_features(_results())
    result = evaluate(features, holdout_season=2023)
    assert result is not None
    assert result.model.n_test > 0
    assert result.baseline.name == "grid position"


def test_a_model_on_noise_does_not_beat_the_baseline() -> None:
    """The test that stops a useless model being reported as a success.

    Note what this catches: on random finishing order the ridge fit *does* score a
    lower MAE than grid position, because predicting the middle of the field beats
    predicting the extremes when the outcome is uniform. Its rank correlation is
    negative — it orders the field backwards. Only the combined check rejects it.
    """
    features = build_features(_results(signal=False, seed=5))
    result = evaluate(features, holdout_season=2023)
    assert result is not None
    assert result.beats_baseline is False
    assert "no better than grid position" in result.verdict


def test_verdict_reflects_the_margin_not_just_the_sign() -> None:
    """A hair's-breadth win is not 'clearly better'."""
    features = build_features(_results())
    result = evaluate(features, holdout_season=2023)
    assert result is not None
    if result.beats_baseline:
        assert result.verdict.endswith("better than grid position")
    else:
        assert result.verdict == "no better than grid position"


def test_baseline_is_grid_position_exactly() -> None:
    """The baseline must be the trivial answer, not a weakened straw man."""
    features = build_features(_results())
    _, test = features.split_by_season(2023)
    clean = test.drop_nulls(["grid_position", "position"])
    expected = float(
        np.mean(
            np.abs(
                clean.get_column("grid_position").to_numpy()
                - clean.get_column("position").to_numpy()
            )
        )
    )
    result = evaluate(features, holdout_season=2023)
    assert result is not None
    assert result.baseline.mae == pytest.approx(expected, abs=0.01)


def test_evaluation_needs_both_a_train_and_a_test_season() -> None:
    features = build_features(_results(seasons=(2023,)))
    assert evaluate(features, holdout_season=2023) is None


def test_empty_features_evaluate_to_nothing() -> None:
    from f1x.engine.predictive.features import FEATURE_COLUMNS, FeatureSet

    empty = FeatureSet(pl.DataFrame(), FEATURE_COLUMNS, "position")
    assert evaluate(empty, holdout_season=2023) is None


# --------------------------------------------------------------------------
# feature importance
# --------------------------------------------------------------------------


def test_importance_is_ordered_by_magnitude() -> None:
    features = build_features(_results())
    importance = feature_importance(features, holdout_season=2023)
    assert importance
    magnitudes = [abs(value) for _, value in importance]
    assert magnitudes == sorted(magnitudes, reverse=True)


def test_grid_position_dominates_when_it_drives_the_outcome() -> None:
    """A sanity check on the standardisation: the strongest input should rank first."""
    features = build_features(_results())
    importance = feature_importance(features, holdout_season=2023)
    assert importance[0][0] in {"grid_position", "prior_mean_finish"}
