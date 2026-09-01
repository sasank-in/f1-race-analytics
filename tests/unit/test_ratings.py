"""Composite driver ratings.

The ratings are relative by construction, so the tests check ordering and fairness
rather than absolute values: does the quicker driver rate higher, does a driver with
missing data avoid being punished for it, and does each component actually influence
the result.
"""

from __future__ import annotations

import polars as pl
import pytest

from f1x.engine.metrics.ratings import MIN_RACES, build_ratings


def _pace(gaps: dict[str, float], spreads: dict[str, float] | None = None,
          n_races: int = 5) -> pl.DataFrame:
    """mart.pace_rankings for a set of drivers."""
    spreads = spreads or dict.fromkeys(gaps, 0.5)
    rows = []
    for driver, gap in gaps.items():
        for race in range(n_races):
            rows.append(
                {
                    "session_id": race + 1,
                    "driver_number": driver,
                    "gap_to_best_s": gap,
                    "std_s": spreads[driver],
                }
            )
    return pl.DataFrame(rows)


def _results(gained: dict[str, float]) -> pl.DataFrame:
    """Grid and finishing positions implying a given average places-gained."""
    rows = []
    for driver, places in gained.items():
        for race in range(5):
            rows.append(
                {
                    "session_id": race + 1,
                    "driver_number": driver,
                    "grid_position": 10.0,
                    "position": 10.0 - places,
                }
            )
    return pl.DataFrame(rows)


def _stints(degradation: dict[str, float]) -> pl.DataFrame:
    rows = []
    for driver, slope in degradation.items():
        for race in range(5):
            rows.append(
                {
                    "session_id": race + 1,
                    "driver_number": driver,
                    "compound": "SOFT",
                    "degradation_s_per_lap": slope,
                    "is_reliable": True,
                }
            )
    return pl.DataFrame(rows)


def test_the_quickest_driver_rates_highest() -> None:
    ratings = build_ratings(
        _pace({"1": 0.0, "2": 0.5, "3": 1.0}),
        _results({"1": 0.0, "2": 0.0, "3": 0.0}),
        _stints({"1": 0.1, "2": 0.1, "3": 0.1}),
    )
    assert ratings[0].driver_number == "1"
    assert ratings[0].rank == 1


def test_ratings_are_ranked_and_ordered() -> None:
    ratings = build_ratings(
        _pace({"1": 0.0, "2": 0.5, "3": 1.0}),
        _results({"1": 0.0, "2": 0.0, "3": 0.0}),
        _stints({"1": 0.1, "2": 0.1, "3": 0.1}),
    )
    assert [r.rank for r in ratings] == [1, 2, 3]
    assert ratings[0].overall >= ratings[1].overall >= ratings[2].overall


def test_racecraft_separates_drivers_with_identical_pace() -> None:
    """The component exists to catch what pace alone misses."""
    ratings = build_ratings(
        _pace({"1": 0.5, "2": 0.5}),
        _results({"1": 4.0, "2": -2.0}),  # one gains places, one loses them
        _stints({"1": 0.1, "2": 0.1}),
    )
    overtaker = next(r for r in ratings if r.driver_number == "1")
    loser = next(r for r in ratings if r.driver_number == "2")
    assert overtaker.racecraft > loser.racecraft
    assert overtaker.overall > loser.overall


def test_consistency_separates_drivers_with_identical_pace() -> None:
    ratings = build_ratings(
        _pace({"1": 0.5, "2": 0.5}, spreads={"1": 0.3, "2": 1.2}),
        _results({"1": 0.0, "2": 0.0}),
        _stints({"1": 0.1, "2": 0.1}),
    )
    steady = next(r for r in ratings if r.driver_number == "1")
    erratic = next(r for r in ratings if r.driver_number == "2")
    assert steady.consistency > erratic.consistency


def test_tyre_management_is_compared_within_compound_and_session() -> None:
    """A gentler slope than the field on the same tyre scores higher."""
    ratings = build_ratings(
        _pace({"1": 0.5, "2": 0.5}),
        _results({"1": 0.0, "2": 0.0}),
        _stints({"1": 0.05, "2": 0.30}),
    )
    gentle = next(r for r in ratings if r.driver_number == "1")
    harsh = next(r for r in ratings if r.driver_number == "2")
    assert gentle.tyre_management > harsh.tyre_management


def test_a_driver_with_no_stint_fits_is_not_punished() -> None:
    """Missing data is missing, not bad. Scoring it at the bottom would be a smear."""
    ratings = build_ratings(
        _pace({"1": 0.5, "2": 0.5}),
        _results({"1": 0.0, "2": 0.0}),
        pl.DataFrame(),  # no stint fits at all
    )
    assert all(r.tyre_management == pytest.approx(50.0) for r in ratings)


def test_drivers_with_too_few_races_are_excluded() -> None:
    """A one-off entry does not have a rating worth reporting."""
    ratings = build_ratings(
        _pace({"1": 0.5, "2": 0.5}, n_races=MIN_RACES - 1),
        pl.DataFrame(),
        pl.DataFrame(),
    )
    assert ratings == []


def test_identical_drivers_all_score_the_midpoint() -> None:
    """No spread means no information, and no information means no separation."""
    ratings = build_ratings(
        _pace({"1": 0.5, "2": 0.5, "3": 0.5}),
        _results({"1": 0.0, "2": 0.0, "3": 0.0}),
        _stints({"1": 0.1, "2": 0.1, "3": 0.1}),
    )
    assert all(r.overall == pytest.approx(50.0) for r in ratings)


def test_strongest_component_is_reported() -> None:
    ratings = build_ratings(
        _pace({"1": 1.0, "2": 0.0}),
        _results({"1": 5.0, "2": -1.0}),
        _stints({"1": 0.1, "2": 0.1}),
    )
    slow_but_scrappy = next(r for r in ratings if r.driver_number == "1")
    assert slow_but_scrappy.strongest == "racecraft"


def test_empty_input_produces_no_ratings() -> None:
    assert build_ratings(pl.DataFrame(), pl.DataFrame(), pl.DataFrame()) == []


def test_weights_can_be_overridden() -> None:
    """The weights are a judgement; changing them must change the answer."""
    pace = _pace({"1": 1.0, "2": 0.0})
    results = _results({"1": 6.0, "2": -2.0})
    stints = _stints({"1": 0.1, "2": 0.1})

    pace_led = build_ratings(pace, results, stints)
    racecraft_led = build_ratings(
        pace,
        results,
        stints,
        weights={
            "pace": 0.10,
            "racecraft": 0.70,
            "consistency": 0.10,
            "tyre_management": 0.10,
        },
    )
    assert pace_led[0].driver_number == "2"
    assert racecraft_led[0].driver_number == "1"
