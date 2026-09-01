"""Cross-season fuel-effect fitting.

The frames here are synthesised with a known fuel coefficient, so a passing test means
the estimator recovers a value it was never told — and the rejection tests confirm it
declines to answer when the data cannot support one.
"""

from __future__ import annotations

import polars as pl
import pytest

from f1x.engine.pace.fuel_fit import (
    DEFAULT_FUEL_EFFECT_S_PER_KG,
    fit_circuit,
)


def _season_laps(
    *,
    season: int,
    total_laps: int,
    fuel_effect: float,
    degradation: float = 0.05,
    n_drivers: int = 10,
    base_lap: float = 90.0,
    stint_length: int = 20,
) -> list[dict[str, object]]:
    """One season at one circuit, built with a known fuel coefficient.

    Fuel burns linearly across the race and tyre age resets at each stint, exactly as
    the transform layer models them.
    """
    rows: list[dict[str, object]] = []
    burn = 100.0 / total_laps
    for driver in range(n_drivers):
        # Season baselines differ; the fit must absorb this rather than attribute it
        # to fuel.
        driver_pace = base_lap + driver * 0.1 + (season - 2022) * 0.8
        for lap in range(1, total_laps + 1):
            fuel = 100.0 - (lap - 1) * burn
            stint = (lap - 1) // stint_length + 1
            age = (lap - 1) % stint_length
            rows.append(
                {
                    "season_year": season,
                    "driver_number": str(driver),
                    "lap_number": lap,
                    "stint": stint,
                    "tyre_life": float(age),
                    "fuel_load_kg": fuel,
                    "lap_time_s": driver_pace + fuel * fuel_effect + age * degradation,
                    "is_representative": True,
                }
            )
    return rows


def test_recovers_a_known_coefficient_from_two_seasons() -> None:
    """Two race lengths break the collinearity that defeats a single-season fit."""
    rows = _season_laps(season=2022, total_laps=57, fuel_effect=0.030) + _season_laps(
        season=2023, total_laps=50, fuel_effect=0.030
    )
    result = fit_circuit(pl.DataFrame(rows), circuit_key="test")
    assert result.fitted is True
    assert result.effect == pytest.approx(0.030, abs=0.005)


def test_recovers_a_different_coefficient() -> None:
    """Not hardcoded to the default: a low-sensitivity circuit fits low."""
    rows = _season_laps(season=2022, total_laps=70, fuel_effect=0.012) + _season_laps(
        season=2023, total_laps=63, fuel_effect=0.012
    )
    result = fit_circuit(pl.DataFrame(rows), circuit_key="test")
    assert result.fitted is True
    assert result.effect == pytest.approx(0.012, abs=0.005)


def test_single_season_is_refused() -> None:
    """The documented failure: fuel is perfectly collinear with lap number."""
    rows = _season_laps(season=2023, total_laps=57, fuel_effect=0.030)
    result = fit_circuit(pl.DataFrame(rows), circuit_key="test")
    assert result.fitted is False
    assert "collinear" in result.reason
    assert result.effect == DEFAULT_FUEL_EFFECT_S_PER_KG


def test_identical_race_lengths_are_refused() -> None:
    """Two seasons of the same distance reproduce the single-season problem exactly."""
    rows = _season_laps(season=2022, total_laps=57, fuel_effect=0.030) + _season_laps(
        season=2023, total_laps=57, fuel_effect=0.030
    )
    result = fit_circuit(pl.DataFrame(rows), circuit_key="test")
    assert result.fitted is False
    assert "identical race lengths" in result.reason


def test_season_pace_differences_do_not_leak_into_the_fuel_term() -> None:
    """A season that is simply slower must not inflate the fuel coefficient."""
    rows = _season_laps(
        season=2022, total_laps=57, fuel_effect=0.030, base_lap=90.0
    ) + _season_laps(season=2023, total_laps=50, fuel_effect=0.030, base_lap=87.0)
    result = fit_circuit(pl.DataFrame(rows), circuit_key="test")
    assert result.fitted is True
    assert result.effect == pytest.approx(0.030, abs=0.008)


def test_degradation_is_fitted_alongside_not_absorbed() -> None:
    """Heavy degradation must not be mistaken for fuel sensitivity."""
    rows = _season_laps(
        season=2022, total_laps=57, fuel_effect=0.030, degradation=0.20
    ) + _season_laps(season=2023, total_laps=50, fuel_effect=0.030, degradation=0.20)
    result = fit_circuit(pl.DataFrame(rows), circuit_key="test")
    assert result.fitted is True
    assert result.effect == pytest.approx(0.030, abs=0.008)


def test_implausible_fit_falls_back_to_the_default() -> None:
    """A fitted number outside physical bounds is discarded, never applied."""
    rows = _season_laps(season=2022, total_laps=57, fuel_effect=0.5) + _season_laps(
        season=2023, total_laps=50, fuel_effect=0.5
    )
    result = fit_circuit(pl.DataFrame(rows), circuit_key="test")
    assert result.fitted is False
    assert "outside plausible range" in result.reason
    assert result.effect == DEFAULT_FUEL_EFFECT_S_PER_KG


def test_thin_data_is_refused() -> None:
    rows = _season_laps(season=2022, total_laps=57, fuel_effect=0.03, n_drivers=1)
    result = fit_circuit(pl.DataFrame(rows), circuit_key="test")
    assert result.fitted is False


def test_empty_frame_is_refused() -> None:
    result = fit_circuit(pl.DataFrame(), circuit_key="test")
    assert result.fitted is False
    assert result.effect == DEFAULT_FUEL_EFFECT_S_PER_KG
