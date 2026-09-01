"""Telemetry alignment, delta time and corner detection.

Traces are synthesised so the right answer is known: a lap built with two corners must
yield two corners, and a lap driven a known amount faster must show that delta.
"""

from __future__ import annotations

import numpy as np
import polars as pl
import pytest

from f1x.engine.telemetry import corners, delta
from f1x.engine.telemetry.alignment import align_lap, integrate_distance


def _synthetic_lap(
    *, n_corners: int = 2, speed_scale: float = 1.0, samples: int = 800
) -> pl.DataFrame:
    """A lap whose speed trace dips once per corner, otherwise near top speed."""
    t = np.linspace(0.0, 90.0, samples)
    # Each corner is a Gaussian dip in an otherwise high-speed trace.
    speed = np.full(samples, 300.0)
    for i in range(n_corners):
        centre = (i + 1) * samples / (n_corners + 1)
        speed -= 180.0 * np.exp(-(((np.arange(samples) - centre) / (samples * 0.04)) ** 2))
    speed *= speed_scale

    brake = np.zeros(samples)
    throttle = np.full(samples, 100.0)
    for i in range(n_corners):
        centre = int((i + 1) * samples / (n_corners + 1))
        width = int(samples * 0.05)
        brake[centre - width : centre] = 1.0
        throttle[centre - width : centre + width // 2] = 0.0

    return pl.DataFrame(
        {
            "session_s": t,
            "speed": speed,
            "throttle": throttle,
            "brake": brake,
            "gear": np.full(samples, 6.0),
        }
    )


# --------------------------------------------------------------------------
# alignment
# --------------------------------------------------------------------------


def test_distance_integrates_from_speed_and_time() -> None:
    """100 km/h for 36 s is one kilometre."""
    speed = np.full(37, 100.0)
    time = np.arange(37, dtype=float)
    assert integrate_distance(speed, time)[-1] == pytest.approx(1000.0, rel=0.02)


def test_dropped_samples_do_not_inject_phantom_distance() -> None:
    """A large time gap is a feed dropout, not thirty seconds of travel."""
    speed = np.full(10, 200.0)
    time = np.array([0, 1, 2, 3, 4, 100, 101, 102, 103, 104], dtype=float)
    distance = integrate_distance(speed, time)
    assert distance[-1] < 1000.0, "a 96 s gap must not be integrated as travel"


def test_aligned_lap_sits_on_an_even_distance_grid() -> None:
    aligned = align_lap(_synthetic_lap(), driver_number="44", lap_number=1)
    assert aligned is not None
    steps = np.diff(aligned.distance_m)
    assert np.allclose(steps, steps[0])


def test_short_trace_is_not_aligned() -> None:
    """An in-lap or a dropout is not a lap and must not be treated as one."""
    frame = pl.DataFrame({"session_s": [0.0, 1.0], "speed": [100.0, 100.0]})
    assert align_lap(frame, driver_number="44", lap_number=1) is None


def test_alignment_needs_the_required_channels() -> None:
    frame = pl.DataFrame({"session_s": np.arange(100.0)})
    assert align_lap(frame, driver_number="44", lap_number=1) is None


# --------------------------------------------------------------------------
# delta time
# --------------------------------------------------------------------------


def test_identical_laps_have_zero_delta() -> None:
    lap = align_lap(_synthetic_lap(), driver_number="44", lap_number=1)
    assert lap is not None
    trace = delta.compute_delta(lap, lap)
    assert trace is not None
    assert trace.final_delta_s == pytest.approx(0.0, abs=1e-6)


def test_faster_lap_shows_a_negative_final_delta() -> None:
    """The comparison lap is quicker, so it finishes ahead of the reference."""
    reference = align_lap(_synthetic_lap(), driver_number="44", lap_number=1)
    quicker = align_lap(
        _synthetic_lap(speed_scale=1.05), driver_number="1", lap_number=1
    )
    assert reference is not None and quicker is not None
    trace = delta.compute_delta(reference, quicker)
    assert trace is not None
    assert trace.final_delta_s < 0


def test_delta_is_truncated_to_the_common_distance() -> None:
    """Two laps rarely measure identically; extrapolating would invent a delta."""
    short = align_lap(_synthetic_lap(samples=400), driver_number="44", lap_number=1)
    long = align_lap(_synthetic_lap(samples=800), driver_number="1", lap_number=1)
    assert short is not None and long is not None
    trace = delta.compute_delta(long, short)
    assert trace is not None
    assert trace.distance_m[-1] <= min(short.lap_distance_m, long.lap_distance_m)


def test_segment_deltas_sum_to_the_final_delta() -> None:
    reference = align_lap(_synthetic_lap(), driver_number="44", lap_number=1)
    quicker = align_lap(_synthetic_lap(speed_scale=1.03), driver_number="1", lap_number=1)
    assert reference is not None and quicker is not None
    trace = delta.compute_delta(reference, quicker)
    assert trace is not None
    total = sum(gain for _, gain in delta.segment_deltas(trace))
    assert total == pytest.approx(trace.final_delta_s, abs=0.05)


# --------------------------------------------------------------------------
# corner detection
# --------------------------------------------------------------------------


def test_detects_the_corners_that_were_synthesised() -> None:
    lap = align_lap(_synthetic_lap(n_corners=3), driver_number="44", lap_number=1)
    assert lap is not None
    found = corners.detect_corners(lap)
    assert len(found) == 3


def test_corner_records_a_minimum_below_its_entry_speed() -> None:
    lap = align_lap(_synthetic_lap(n_corners=2), driver_number="44", lap_number=1)
    assert lap is not None
    for corner in corners.detect_corners(lap):
        assert corner.min_speed_kmh < corner.entry_speed_kmh
        assert corner.speed_drop_kmh > 0


def test_flat_lap_has_no_corners() -> None:
    """A constant-speed trace is an oval run flat, not a lap with corners."""
    frame = pl.DataFrame(
        {
            "session_s": np.linspace(0, 60, 500),
            "speed": np.full(500, 280.0),
            "throttle": np.full(500, 100.0),
            "brake": np.zeros(500),
            "gear": np.full(500, 8.0),
        }
    )
    lap = align_lap(frame, driver_number="44", lap_number=1)
    assert lap is not None
    assert corners.detect_corners(lap) == []


def test_braking_point_precedes_the_apex() -> None:
    lap = align_lap(_synthetic_lap(n_corners=2), driver_number="44", lap_number=1)
    assert lap is not None
    for corner in corners.detect_corners(lap):
        if corner.braking_point_m is not None:
            assert corner.braking_point_m < corner.apex_distance_m


def test_corners_are_matched_between_two_laps() -> None:
    reference = align_lap(_synthetic_lap(n_corners=3), driver_number="44", lap_number=1)
    comparison = align_lap(
        _synthetic_lap(n_corners=3, speed_scale=1.02), driver_number="1", lap_number=1
    )
    assert reference is not None and comparison is not None
    matches = corners.compare_corners(
        corners.detect_corners(reference), corners.detect_corners(comparison)
    )
    assert len(matches) == 3
    # The comparison lap carries more speed, so every delta should be positive.
    assert all(difference > 0 for _, _, difference in matches)
