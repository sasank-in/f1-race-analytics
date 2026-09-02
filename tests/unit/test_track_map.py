"""Track geometry reconstruction.

The circuit is drawn from where the car went, so these check the two things that
would silently distort it: the aspect ratio, and the join between two feeds that
sample independently.
"""

from __future__ import annotations

import numpy as np
import polars as pl
import pytest

from f1x.engine.telemetry.track_map import MAP_SIZE, build_map, downsample, normalise


def _oval(n: int = 200, width: float = 4000.0, height: float = 2000.0) -> tuple:
    t = np.linspace(0, 2 * np.pi, n)
    return width / 2 * np.cos(t), height / 2 * np.sin(t)


def test_normalise_preserves_aspect_ratio() -> None:
    """A wide circuit must not be stretched square, or Monaco looks like Spa."""
    x, y = _oval(width=4000.0, height=2000.0)
    nx, ny = normalise(x, y)
    assert (nx.max() - nx.min()) == pytest.approx(MAP_SIZE, abs=1)
    # Half the width, so half the box.
    assert (ny.max() - ny.min()) == pytest.approx(MAP_SIZE / 2, abs=1)


def test_normalise_centres_the_shorter_axis() -> None:
    x, y = _oval(width=4000.0, height=2000.0)
    _, ny = normalise(x, y)
    assert ny.min() == pytest.approx(MAP_SIZE / 4, abs=2)
    assert ny.max() == pytest.approx(3 * MAP_SIZE / 4, abs=2)


def test_normalise_handles_an_empty_trace() -> None:
    empty = np.array([])
    nx, ny = normalise(empty, empty)
    assert nx.size == 0 and ny.size == 0


def _frames(n: int = 200) -> tuple[pl.DataFrame, pl.DataFrame]:
    x, y = _oval(n)
    t = np.linspace(0, 90, n)
    positions = pl.DataFrame({"session_s": t, "x": x, "y": y})
    # Telemetry samples on its own clock, deliberately offset and at a different rate.
    car_t = np.linspace(0, 90, n // 2)
    telemetry = pl.DataFrame(
        {"session_s": car_t, "speed": 200 + 80 * np.sin(car_t / 5)}
    )
    return positions, telemetry


def test_speed_is_interpolated_onto_the_position_clock() -> None:
    """The two feeds sample independently, so they must be joined by time."""
    positions, telemetry = _frames()
    track = build_map(positions, telemetry, driver_number="1", lap_number=1)
    assert track is not None
    # One speed value per position sample, not per telemetry sample.
    assert track.speed_kmh.size == track.x.size
    assert track.speed_kmh.max() > 200


def test_distance_accumulates_along_the_line() -> None:
    positions, telemetry = _frames()
    track = build_map(positions, telemetry, driver_number="1", lap_number=1)
    assert track is not None
    assert track.lap_distance_m > 0
    # Monotonic: distance never goes backwards along a lap.
    assert np.all(np.diff(track.distance_m) >= 0)


def test_a_corner_distance_maps_back_to_a_coordinate() -> None:
    """Corner markers depend on translating distance back onto the geometry."""
    positions, telemetry = _frames()
    track = build_map(positions, telemetry, driver_number="1", lap_number=1)
    assert track is not None
    x, y = track.point_at_distance(track.lap_distance_m / 2)
    assert 0 <= x <= MAP_SIZE
    assert 0 <= y <= MAP_SIZE


def test_a_lap_without_speed_still_draws() -> None:
    """Geometry is the point; missing speed should grey the map, not lose it."""
    positions, _ = _frames()
    track = build_map(positions, pl.DataFrame(), driver_number="1", lap_number=1)
    assert track is not None
    assert track.n_points > 0


def test_a_fragment_is_not_a_lap() -> None:
    positions = pl.DataFrame({"session_s": [0.0, 1.0], "x": [0.0, 1.0], "y": [0.0, 1.0]})
    assert build_map(positions, pl.DataFrame(), driver_number="1", lap_number=1) is None


def test_downsampling_keeps_the_shape() -> None:
    positions, telemetry = _frames(n=1200)
    track = build_map(positions, telemetry, driver_number="1", lap_number=1)
    assert track is not None
    thinned = downsample(track, target=300)
    assert thinned.n_points <= 400
    # The lap still spans the same box after thinning.
    assert thinned.x.max() == pytest.approx(track.x.max(), abs=20)
