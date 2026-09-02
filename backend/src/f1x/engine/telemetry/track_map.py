"""Track geometry from positional data.

The circuit is not stored anywhere — it is reconstructed from the x/y trace a car
leaves as it drives round. That means the engine draws any circuit it has data for,
including ones added to the calendar after this was written, with no map to maintain.

Two things have to be joined to make the map useful. Position and car telemetry arrive
on *separate* feeds with their own sample times, so speed has to be interpolated onto
the position samples rather than assumed to line up. And the raw coordinates are in
tenths of a metre with an arbitrary origin, so they are normalised to a fixed box that
a viewport can draw without knowing anything about the circuit.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import polars as pl

# The coordinate box the map is normalised into. Square, so a circuit's aspect ratio
# is preserved rather than stretched to fill whatever element draws it.
MAP_SIZE = 1000.0

# Below this a lap is a fragment: an in-lap, an out-lap, or a feed dropout.
MIN_POSITION_SAMPLES = 50


@dataclass(frozen=True)
class TrackMap:
    """One lap's racing line with speed carried along it."""

    driver_number: str
    lap_number: int

    # Normalised coordinates, both in 0..MAP_SIZE with the circuit centred.
    x: np.ndarray
    y: np.ndarray
    speed_kmh: np.ndarray
    distance_m: np.ndarray

    @property
    def n_points(self) -> int:
        return int(self.x.size)

    @property
    def lap_distance_m(self) -> float:
        return float(self.distance_m[-1]) if self.distance_m.size else 0.0

    def point_at_distance(self, metres: float) -> tuple[float, float]:
        """Map coordinate at a given distance into the lap.

        Used to place corner markers: the corner detector works in distance, and this
        translates that back to somewhere on the drawing.
        """
        if self.distance_m.size == 0:
            return (0.0, 0.0)
        index = int(np.argmin(np.abs(self.distance_m - metres)))
        return (float(self.x[index]), float(self.y[index]))


def normalise(
    x: np.ndarray, y: np.ndarray, *, size: float = MAP_SIZE
) -> tuple[np.ndarray, np.ndarray]:
    """Fit a circuit into a square box without distorting its shape.

    Both axes are scaled by the *same* factor and the shorter one centred, so Monaco
    stays cramped and Spa stays long rather than both being stretched to fill the box.
    """
    if x.size == 0:
        return x, y

    width = float(x.max() - x.min())
    height = float(y.max() - y.min())
    span = max(width, height, 1.0)
    scale = size / span

    # Centre the shorter axis in the leftover room.
    offset_x = (size - width * scale) / 2.0
    offset_y = (size - height * scale) / 2.0

    return (
        (x - x.min()) * scale + offset_x,
        (y - y.min()) * scale + offset_y,
    )


def build_map(
    positions: pl.DataFrame,
    telemetry: pl.DataFrame,
    *,
    driver_number: str,
    lap_number: int,
) -> TrackMap | None:
    """Join a lap's positional trace to its speed trace and normalise the result.

    Both frames carry ``session_s``; the two feeds sample independently, so speed is
    interpolated onto the position timestamps. Interpolating the other way would move
    the geometry, which is the thing being drawn.
    """
    if positions.is_empty() or len(positions) < MIN_POSITION_SAMPLES:
        return None
    if not {"session_s", "x", "y"} <= set(positions.columns):
        return None

    ordered = positions.sort("session_s").drop_nulls(["session_s", "x", "y"])
    if len(ordered) < MIN_POSITION_SAMPLES:
        return None

    session_s = ordered.get_column("session_s").to_numpy().astype(float)
    x = ordered.get_column("x").to_numpy().astype(float)
    y = ordered.get_column("y").to_numpy().astype(float)

    # Speed onto the position clock.
    if telemetry.is_empty() or "speed" not in telemetry.columns:
        speed = np.zeros_like(session_s)
    else:
        car = telemetry.sort("session_s").drop_nulls(["session_s", "speed"])
        speed = (
            np.interp(
                session_s,
                car.get_column("session_s").to_numpy().astype(float),
                car.get_column("speed").to_numpy().astype(float),
            )
            if len(car) >= 2
            else np.zeros_like(session_s)
        )

    # Distance along the line, from the geometry itself rather than integrated speed.
    # Coordinates are tenths of a metre.
    steps = np.hypot(np.diff(x), np.diff(y)) / 10.0
    distance = np.concatenate([[0.0], np.cumsum(steps)])

    normalised_x, normalised_y = normalise(x, y)

    return TrackMap(
        driver_number=driver_number,
        lap_number=lap_number,
        x=normalised_x,
        y=normalised_y,
        speed_kmh=speed,
        distance_m=distance,
    )


def downsample(track: TrackMap, target: int = 400) -> TrackMap:
    """Thin the trace for transport.

    A lap is a few hundred samples already, but a long circuit at a high sample rate
    can run past a thousand, and no viewport resolves that. Evenly spaced so the
    shape survives.
    """
    if track.n_points <= target:
        return track
    step = max(1, track.n_points // target)
    return TrackMap(
        driver_number=track.driver_number,
        lap_number=track.lap_number,
        x=track.x[::step],
        y=track.y[::step],
        speed_kmh=track.speed_kmh[::step],
        distance_m=track.distance_m[::step],
    )
