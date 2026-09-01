"""Pace and degradation models, checked on constructed frames.

Each test builds a stint whose answer is known by construction, so a failure points at
the model rather than at the data.
"""

from __future__ import annotations

import numpy as np
import polars as pl
import pytest

from f1x.engine.degradation.curves import MIN_STINTS_PER_COMPOUND, build_curves
from f1x.engine.pace import fuel_model, ranking, stint_model
from f1x.engine.pace.stint_model import fit_session, fit_stint, to_frame

# --------------------------------------------------------------------------
# stint regression
# --------------------------------------------------------------------------


def _fit(times: list[float], age: list[float]) -> stint_model.StintFit | None:
    return fit_stint(
        np.array(times),
        np.array(age),
        session_id=1,
        driver_number="44",
        stint=1,
        compound="SOFT",
    )


def test_regression_recovers_a_known_slope_and_intercept() -> None:
    """A stint built as 90 + 0.1 per lap must come back as pace 90, degradation 0.1."""
    age = [1.0, 2.0, 3.0, 4.0, 5.0, 6.0]
    times = [90.0 + 0.1 * a for a in age]
    fit = _fit(times, age)
    assert fit is not None
    assert fit.pace_s == pytest.approx(90.0, abs=1e-6)
    assert fit.degradation_s_per_lap == pytest.approx(0.1, abs=1e-6)
    assert fit.r_squared == pytest.approx(1.0, abs=1e-9)


def test_pace_is_the_zero_age_intercept_not_the_average() -> None:
    """The whole point of the split: pace excludes the degradation already accrued."""
    age = [1.0, 2.0, 3.0, 4.0, 5.0]
    times = [90.0 + 0.5 * a for a in age]
    fit = _fit(times, age)
    assert fit is not None
    assert fit.pace_s == pytest.approx(90.0, abs=1e-6)
    assert fit.pace_s < float(np.mean(times))


def test_short_stint_is_not_fitted() -> None:
    """Three points define a line with no residual left to judge it by."""
    assert _fit([90.0, 90.1, 90.2], [1.0, 2.0, 3.0]) is None


def test_constant_tyre_age_is_not_fitted() -> None:
    """Without variation in age there is no slope to recover."""
    assert _fit([90.0] * 6, [1.0] * 6) is None


def test_identical_lap_times_give_zero_not_undefined_r_squared() -> None:
    """Zero variance makes r-squared undefined; it must not become NaN or 1.0."""
    fit = _fit([90.0] * 6, [1.0, 2.0, 3.0, 4.0, 5.0, 6.0])
    assert fit is not None
    assert fit.r_squared == 0.0
    assert fit.degradation_s_per_lap == pytest.approx(0.0, abs=1e-9)


def test_implausible_degradation_is_flagged_unreliable() -> None:
    """A 3 s/lap slope is a damaged car, not tyre wear, and must not reach strategy."""
    age = [1.0, 2.0, 3.0, 4.0, 5.0, 6.0]
    fit = _fit([90.0 + 3.0 * a for a in age], age)
    assert fit is not None
    assert fit.is_reliable is False


def test_nan_laps_are_dropped_before_fitting() -> None:
    age = [1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0]
    times = [90.1, 90.2, float("nan"), 90.4, 90.5, 90.6, 90.7]
    fit = _fit(times, age)
    assert fit is not None
    assert fit.n_laps == 6


# --------------------------------------------------------------------------
# session fitting
# --------------------------------------------------------------------------


def _session_frame(n_drivers: int = 2, n_laps: int = 8) -> pl.DataFrame:
    rows = []
    for driver in range(n_drivers):
        for lap in range(1, n_laps + 1):
            rows.append(
                {
                    "session_id": 1,
                    "driver_number": str(44 + driver),
                    "lap_number": lap,
                    "stint": 1,
                    "compound": "SOFT",
                    "tyre_life": float(lap),
                    "fuel_corrected_s": 90.0 + driver * 0.5 + 0.1 * lap,
                    "lap_time_s": 92.0 + driver * 0.5,
                    "is_representative": True,
                    "is_clean_air": True,
                }
            )
    return pl.DataFrame(rows)


def test_session_fit_produces_one_row_per_stint() -> None:
    fits = fit_session(_session_frame(n_drivers=3))
    assert len(fits) == 3


def test_excluded_laps_do_not_enter_a_fit() -> None:
    frame = _session_frame().with_columns(
        is_representative=pl.col("lap_number") > 2  # first two laps excluded
    )
    fits = fit_session(frame)
    assert all(fit.n_laps == 6 for fit in fits)


def test_empty_session_fits_nothing() -> None:
    assert fit_session(pl.DataFrame()) == []


def test_to_frame_of_no_fits_has_the_right_schema() -> None:
    frame = to_frame([])
    assert frame.is_empty()
    assert "degradation_s_per_lap" in frame.columns


# --------------------------------------------------------------------------
# degradation curves
# --------------------------------------------------------------------------


def _fits_frame(slopes: list[float], compound: str = "SOFT") -> pl.DataFrame:
    return pl.DataFrame(
        [
            {
                "session_id": 1,
                "driver_number": str(40 + i),
                "stint": 1,
                "compound": compound,
                "n_laps": 10,
                "pace_s": 90.0,
                "degradation_s_per_lap": slope,
                "r_squared": 0.9,
                "residual_std_s": 0.1,
                "tyre_age_start": 1.0,
                "is_reliable": True,
            }
            for i, slope in enumerate(slopes)
        ]
    )


def test_curve_uses_the_median_slope() -> None:
    """Median, not mean: one damaged car must not define the compound."""
    curves = build_curves(_fits_frame([0.10, 0.11, 0.12, 0.13, 5.0]))
    assert len(curves) == 1
    assert curves[0].degradation_s_per_lap == pytest.approx(0.12)


def test_curve_reports_spread_alongside_the_centre() -> None:
    """A strategy model needs the width, or it presents a window as a single lap."""
    curves = build_curves(_fits_frame([0.10, 0.15, 0.20, 0.25]))
    assert curves[0].degradation_iqr_s > 0


def test_compound_with_too_few_stints_is_skipped() -> None:
    assert build_curves(_fits_frame([0.1] * (MIN_STINTS_PER_COMPOUND - 1))) == []


def test_unreliable_fits_are_excluded_from_curves() -> None:
    frame = _fits_frame([0.1, 0.11, 0.12, 0.13]).with_columns(is_reliable=pl.lit(False))
    assert build_curves(frame) == []


def test_predicted_loss_scales_with_tyre_age() -> None:
    curve = build_curves(_fits_frame([0.10, 0.10, 0.10]))[0]
    assert curve.loss_after(10) == pytest.approx(1.0)
    assert curve.loss_after(20) == pytest.approx(2.0)


# --------------------------------------------------------------------------
# pace ranking
# --------------------------------------------------------------------------


def test_ranking_orders_by_pace_and_reports_gaps() -> None:
    ranked = ranking.rank_session(_session_frame(n_drivers=3, n_laps=12))
    assert [entry.rank for entry in ranked] == [1, 2, 3]
    assert ranked[0].gap_to_best_s == pytest.approx(0.0)
    assert ranked[1].gap_to_best_s > 0
    assert ranked[0].pace_s < ranked[1].pace_s


def test_driver_with_too_few_clean_laps_is_not_ranked() -> None:
    """An early retirement did not produce a representative race."""
    assert ranking.rank_session(_session_frame(n_drivers=2, n_laps=6)) == []


def test_ranking_falls_back_to_raw_times_without_a_corrected_column() -> None:
    """Qualifying runs light, so there is no fuel effect to remove."""
    frame = _session_frame(n_laps=12).drop("fuel_corrected_s")
    assert len(ranking.rank_session(frame)) == 2


def test_ranking_is_robust_to_one_exceptional_lap() -> None:
    """Quantile, not minimum: a single flying lap must not define a driver's pace."""
    frame = _session_frame(n_drivers=1, n_laps=20)
    with_outlier = frame.with_columns(
        fuel_corrected_s=pl.when(pl.col("lap_number") == 1)
        .then(pl.lit(80.0))
        .otherwise(pl.col("fuel_corrected_s"))
    )
    baseline = ranking.rank_session(frame)[0].pace_s
    shifted = ranking.rank_session(with_outlier)[0].pace_s
    assert abs(shifted - baseline) < 0.5, "one fast lap should not move the rating"


# --------------------------------------------------------------------------
# fuel model
# --------------------------------------------------------------------------


def test_fuel_fit_falls_back_to_the_default_on_thin_data() -> None:
    fit = fuel_model.fit_fuel_effect(pl.DataFrame(), circuit_key="test", session_id=1)
    assert fit.fitted is False
    assert fit.effect == fuel_model.DEFAULT_FUEL_EFFECT_S_PER_KG


def test_implausible_fuel_effect_is_rejected_not_applied() -> None:
    """Fuel load is collinear with lap number, so the raw fit lands near 1.0 s/kg.

    The bound is what stops that number reaching every lap of a race.
    """
    rows = []
    for driver in range(10):
        for lap in range(1, 41):
            rows.append(
                {
                    "session_id": 1,
                    "driver_number": str(driver),
                    "stint": 1 + lap // 20,
                    "tyre_life": float(lap % 20 + 1),
                    "fuel_load_kg": 100.0 - lap * 2.0,
                    "lap_time_s": 90.0 + lap * 0.05,
                    "is_representative": True,
                }
            )
    fit = fuel_model.fit_fuel_effect(pl.DataFrame(rows), circuit_key="test", session_id=1)
    assert fit.effect == fuel_model.DEFAULT_FUEL_EFFECT_S_PER_KG
    if not fit.fitted:
        assert fit.reason
