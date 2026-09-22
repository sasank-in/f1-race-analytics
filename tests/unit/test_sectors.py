"""Sector profiles, checked on constructed cases with known answers."""

from __future__ import annotations

import polars as pl
import pytest

from f1x.engine.pace.sectors import (
    MIN_LAPS_FOR_SECTORS,
    build_sector_profiles,
)


def _laps(rows: list[tuple[str, float, float, float]], repeat: int = 10) -> pl.DataFrame:
    """A session where each driver runs the same sector times every lap."""
    records = []
    for driver, s1, s2, s3 in rows:
        for lap in range(repeat):
            records.append(
                {
                    "driver_number": driver,
                    "lap_number": lap + 1,
                    "sector1_s": s1,
                    "sector2_s": s2,
                    "sector3_s": s3,
                }
            )
    return pl.DataFrame(records)


def test_no_laps_yields_no_profiles() -> None:
    assert build_sector_profiles(pl.DataFrame()) == []


def test_missing_sector_columns_yields_no_profiles() -> None:
    """A session ingested without sector times must not produce a half-built profile."""
    frame = pl.DataFrame({"driver_number": ["1"], "lap_number": [1]})
    assert build_sector_profiles(frame) == []


def test_gaps_are_measured_per_sector_not_on_the_total() -> None:
    """The point of the view: the quickest S1 and quickest S2 can be different cars."""
    profiles = build_sector_profiles(
        _laps(
            [
                ("1", 30.0, 43.0, 24.0),  # best S1
                ("2", 31.0, 42.0, 24.0),  # best S2
                ("3", 31.0, 43.0, 23.0),  # best S3
            ]
        )
    )
    by_driver = {p.driver_number: p for p in profiles}

    assert by_driver["1"].gap1_s == pytest.approx(0.0)
    assert by_driver["2"].gap2_s == pytest.approx(0.0)
    assert by_driver["3"].gap3_s == pytest.approx(0.0)
    # And each is measurably off the benchmark in the sectors they do not own.
    assert by_driver["1"].gap2_s == pytest.approx(1.0)
    assert by_driver["1"].gap3_s == pytest.approx(1.0)


def test_strongest_and_weakest_sectors_are_identified() -> None:
    profiles = build_sector_profiles(
        _laps(
            [
                ("1", 30.0, 42.0, 24.0),
                # Level in S1 and S2, half a second down in S3.
                ("2", 30.0, 42.0, 24.5),
            ]
        )
    )
    shaped = next(p for p in profiles if p.driver_number == "2")
    assert shaped.weakest_sector == 3
    assert shaped.strongest_sector in (1, 2)
    assert shaped.spread_s == pytest.approx(0.5)


def test_a_uniformly_slower_car_has_no_shape() -> None:
    """Being slower everywhere is not a weakness in one sector, and must not read as one."""
    profiles = build_sector_profiles(
        _laps([("1", 30.0, 42.0, 24.0), ("2", 30.2, 42.2, 24.2)])
    )
    uniform = next(p for p in profiles if p.driver_number == "2")
    assert uniform.spread_s == pytest.approx(0.0, abs=1e-9)


def test_profiles_are_ordered_by_total_sector_time() -> None:
    profiles = build_sector_profiles(
        _laps(
            [
                ("slow", 31.0, 43.0, 25.0),
                ("quick", 30.0, 42.0, 24.0),
                ("mid", 30.5, 42.5, 24.5),
            ]
        )
    )
    assert [p.driver_number for p in profiles] == ["quick", "mid", "slow"]


def test_a_driver_with_too_few_laps_is_excluded() -> None:
    """A profile from two laps describes two laps, not a car."""
    frame = pl.concat(
        [
            _laps([("regular", 30.0, 42.0, 24.0)], repeat=MIN_LAPS_FOR_SECTORS),
            _laps([("sparse", 29.0, 41.0, 23.0)], repeat=MIN_LAPS_FOR_SECTORS - 1),
        ]
    )
    drivers = [p.driver_number for p in build_sector_profiles(frame)]
    assert drivers == ["regular"]


def test_laps_missing_one_sector_are_dropped_whole() -> None:
    """Mixing samples would build a profile no single lap ever matched."""
    frame = _laps([("1", 30.0, 42.0, 24.0)], repeat=MIN_LAPS_FOR_SECTORS)
    partial = pl.DataFrame(
        [
            {
                "driver_number": "1",
                "lap_number": 99,
                "sector1_s": 25.0,  # an implausibly quick S1...
                "sector2_s": None,  # ...on a lap with no S2
                "sector3_s": 24.0,
            }
        ],
        schema=frame.schema,
    )

    profiles = build_sector_profiles(pl.concat([frame, partial]))
    assert len(profiles) == 1
    # The 25.0 must not have reached the S1 quantile.
    assert profiles[0].sector1_s == pytest.approx(30.0)


def test_total_is_the_sum_of_the_three_sectors() -> None:
    profiles = build_sector_profiles(_laps([("1", 30.0, 42.0, 24.0)]))
    assert profiles[0].total_s == pytest.approx(96.0)
