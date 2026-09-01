"""Monte Carlo race and championship simulation."""

from __future__ import annotations

import numpy as np
import pytest

from f1x.engine.simulation.championship import (
    DriverEntry,
    simulate_championship,
)
from f1x.engine.simulation.race import (
    RaceConditions,
    compare_strategies,
    simulate_strategy,
)


def _conditions(**kw: object) -> RaceConditions:
    params: dict[str, object] = {
        "total_laps": 57,
        "base_lap_s": 95.0,
        "net_pit_loss_s": 25.0,
        "degradation_s_per_lap": 0.15,
    }
    params.update(kw)
    return RaceConditions(**params)  # type: ignore[arg-type]


# --------------------------------------------------------------------------
# race simulation
# --------------------------------------------------------------------------


def test_simulation_is_reproducible_with_a_seed() -> None:
    """Without this, comparing two strategies compares two different sets of luck."""
    first = simulate_strategy(_conditions(), (29, 28), iterations=200, seed=1)
    second = simulate_strategy(_conditions(), (29, 28), iterations=200, seed=1)
    assert first.median_s == second.median_s


def test_race_time_is_roughly_laps_times_pace() -> None:
    """A sanity check that the walk over laps accumulates the right order of magnitude."""
    result = simulate_strategy(
        _conditions(degradation_s_per_lap=0.0, safety_car_probability=0.0),
        (57,),
        iterations=200,
        seed=1,
    )
    assert result.median_s == pytest.approx(57 * 95.0, rel=0.01)


def test_more_stops_cost_more_pit_time() -> None:
    zero_deg = _conditions(degradation_s_per_lap=0.0, safety_car_probability=0.0)
    one = simulate_strategy(zero_deg, (29, 28), iterations=300, seed=2)
    two = simulate_strategy(zero_deg, (19, 19, 19), iterations=300, seed=2)
    assert two.median_s > one.median_s


def test_high_degradation_favours_stopping_more() -> None:
    high = _conditions(degradation_s_per_lap=0.5, safety_car_probability=0.0)
    one = simulate_strategy(high, (29, 28), iterations=300, seed=3)
    two = simulate_strategy(high, (19, 19, 19), iterations=300, seed=3)
    assert two.median_s < one.median_s


def test_negative_degradation_is_clamped() -> None:
    """A tyre never gains time by ageing, whatever the fitted slope says."""
    negative = simulate_strategy(
        _conditions(degradation_s_per_lap=-0.05, safety_car_probability=0.0),
        (57,),
        iterations=200,
        seed=4,
    )
    flat = simulate_strategy(
        _conditions(degradation_s_per_lap=0.0, safety_car_probability=0.0),
        (57,),
        iterations=200,
        seed=4,
    )
    assert negative.median_s == pytest.approx(flat.median_s, abs=0.5)


def test_safety_car_appears_at_the_configured_rate() -> None:
    result = simulate_strategy(
        _conditions(safety_car_probability=0.6), (29, 28), iterations=2000, seed=5
    )
    assert result.safety_car_rate == pytest.approx(0.6, abs=0.05)


def test_safety_car_does_not_dominate_the_variance() -> None:
    """It slows every car equally, so it must not swamp the seconds between strategies.

    Modelled as a large absolute penalty it produced a 170 s spread, which made every
    race a coin toss regardless of the strategies being compared.
    """
    with_sc = simulate_strategy(
        _conditions(safety_car_probability=1.0), (19, 19, 19), iterations=500, seed=6
    )
    assert with_sc.spread_s < 60.0


def test_safety_car_makes_a_stop_cheaper() -> None:
    """The real strategic significance of a safety car is the discounted stop."""
    never = simulate_strategy(
        _conditions(safety_car_probability=0.0), (19, 19, 19), iterations=800, seed=7
    )
    always = simulate_strategy(
        _conditions(safety_car_probability=1.0), (19, 19, 19), iterations=800, seed=7
    )
    assert always.median_s <= never.median_s


def test_zero_distance_race_is_rejected() -> None:
    with pytest.raises(ValueError, match="at least one lap"):
        simulate_strategy(_conditions(), (), iterations=10, seed=1)


def test_comparison_win_rates_sum_to_one() -> None:
    comparison = compare_strategies(
        _conditions(), [(57,), (29, 28), (19, 19, 19)], iterations=300, seed=8
    )
    assert sum(comparison.win_rates.values()) == pytest.approx(1.0)


def test_close_strategies_are_reported_as_not_decisive() -> None:
    """A 51 % edge is a coin toss and must not be presented as a decision."""
    # Zero degradation and zero pit loss makes every strategy equivalent.
    comparison = compare_strategies(
        _conditions(degradation_s_per_lap=0.0, net_pit_loss_s=0.0),
        [(29, 28), (19, 19, 19)],
        iterations=500,
        seed=9,
    )
    assert comparison.is_decisive is False


def test_comparison_of_nothing_is_empty() -> None:
    comparison = compare_strategies(_conditions(), [], iterations=10, seed=1)
    assert comparison.win_rates == {}


# --------------------------------------------------------------------------
# championship
# --------------------------------------------------------------------------


def _entries(n: int = 5) -> list[DriverEntry]:
    return [
        DriverEntry(driver_number=str(i), current_points=100.0 - i * 20, pace_gap_s=i * 0.3)
        for i in range(n)
    ]


def test_leader_with_the_best_pace_is_the_favourite() -> None:
    result = simulate_championship(_entries(), races_remaining=5, iterations=500, seed=1)
    assert result.favourite == "0"


def test_title_probabilities_sum_to_one() -> None:
    result = simulate_championship(_entries(), races_remaining=5, iterations=500, seed=2)
    assert sum(result.title_probability.values()) == pytest.approx(1.0)


def test_a_points_deficit_can_be_overturned_by_pace() -> None:
    """A quick driver behind on points must retain a real chance with races left."""
    entries = [
        DriverEntry(driver_number="slow_leader", current_points=60.0, pace_gap_s=0.8),
        DriverEntry(driver_number="fast_chaser", current_points=0.0, pace_gap_s=0.0),
    ]
    result = simulate_championship(entries, races_remaining=10, iterations=800, seed=3)
    assert result.title_probability["fast_chaser"] > 0.5


def test_more_races_give_the_chaser_more_chance() -> None:
    entries = [
        DriverEntry(driver_number="leader", current_points=80.0, pace_gap_s=0.5),
        DriverEntry(driver_number="chaser", current_points=0.0, pace_gap_s=0.0),
    ]
    short = simulate_championship(entries, races_remaining=2, iterations=600, seed=4)
    long = simulate_championship(entries, races_remaining=12, iterations=600, seed=4)
    assert long.title_probability["chaser"] > short.title_probability["chaser"]


def test_saturated_probability_is_not_called_mathematically_decided() -> None:
    """100 % sampled is not the same as uncatchable, and must not be reported as such."""
    entries = [
        DriverEntry(driver_number="1", current_points=229.0, pace_gap_s=0.0),
        DriverEntry(driver_number="11", current_points=148.0, pace_gap_s=0.35),
    ]
    result = simulate_championship(entries, races_remaining=12, iterations=500, seed=5)
    assert result.title_probability["1"] > 0.95
    assert result.is_mathematically_decided is False


def test_an_uncatchable_lead_is_mathematically_decided() -> None:
    entries = [
        DriverEntry(driver_number="1", current_points=500.0, pace_gap_s=0.0),
        DriverEntry(driver_number="11", current_points=10.0, pace_gap_s=1.0),
    ]
    result = simulate_championship(entries, races_remaining=1, iterations=200, seed=6)
    assert result.is_mathematically_decided is True


def test_empty_field_projects_nothing() -> None:
    result = simulate_championship([], races_remaining=5, iterations=100, seed=1)
    assert result.title_probability == {}


def test_expected_points_exceed_the_starting_total() -> None:
    result = simulate_championship(_entries(), races_remaining=5, iterations=300, seed=7)
    assert result.expected_points["0"] > 100.0


def test_pace_sensitivity_is_calibrated_to_an_observed_season() -> None:
    """2023's fastest car won 19 of 22 races; the sampler should reproduce that."""
    from f1x.engine.simulation.championship import PACE_SENSITIVITY, _finishing_order

    # Pace gaps measured by this engine across 2023.
    gaps = np.array([0.04, 0.35, 0.58, 0.65, 0.70, 0.76, 0.76, 0.78, 1.03, 1.04])
    rng = np.random.default_rng(0)
    wins = sum(
        1 for _ in range(2000) if _finishing_order(gaps, rng, PACE_SENSITIVITY)[0] == 0
    )
    assert 0.75 <= wins / 2000 <= 0.92
