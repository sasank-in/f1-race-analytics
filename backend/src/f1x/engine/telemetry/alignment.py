"""Aligning two laps so their telemetry can be compared.

Telemetry is sampled in time, but a lap comparison has to happen in *distance*. Two
drivers at the same moment are at different points on the track, so comparing their
speed traces by timestamp compares unrelated corners.

Resampling both laps onto a common distance grid fixes that: at 1,200 m into the lap,
both traces describe the same braking zone. Everything downstream — delta time, corner
speeds, racing lines — depends on this step being right.

Distance is integrated from speed rather than taken from the feed. Speed is reported
directly and reliably; a distance channel is not always present, and integrating gives
a consistent basis across sessions.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import polars as pl

# Resolution of the common grid. One metre resolves a braking point to within a car
# length while keeping a full lap in a few thousand points.
GRID_STEP_M = 1.0

# Fewer samples than this is not a lap, it is a fragment.
MIN_SAMPLES = 50


@dataclass(frozen=True)
class AlignedLap:
    """One lap resampled onto a distance grid."""

    driver_number: str
    lap_number: int
    distance_m: np.ndarray
    speed_kmh: np.ndarray
    throttle: np.ndarray
    brake: np.ndarray
    gear: np.ndarray
    # Elapsed time at each distance point, from the lap's start.
    elapsed_s: np.ndarray

    @property
    def lap_distance_m(self) -> float:
        return float(self.distance_m[-1]) if self.distance_m.size else 0.0


def integrate_distance(speed_kmh: np.ndarray, session_s: np.ndarray) -> np.ndarray:
    """Cumulative distance from a speed trace, in metres.

    Trapezoidal integration of speed over time. Sample intervals are irregular in the
    source feed, so the time deltas must be used rather than assuming a fixed rate.
    """
    if speed_kmh.size < 2:
        return np.zeros_like(speed_kmh)
    speed_ms = speed_kmh / 3.6
    dt = np.diff(session_s, prepend=session_s[0])
    # A negative or absurd gap means a dropped sample; treat it as no elapsed time
    # rather than letting it inject phantom distance.
    dt = np.clip(dt, 0.0, 1.0)
    mean_speed = (speed_ms + np.roll(speed_ms, 1)) / 2.0
    mean_speed[0] = speed_ms[0]
    return np.cumsum(mean_speed * dt)


def align_lap(
    telemetry: pl.DataFrame,
    *,
    driver_number: str,
    lap_number: int,
    grid_step_m: float = GRID_STEP_M,
) -> AlignedLap | None:
    """Resample one lap's telemetry onto an even distance grid.

    Returns None when the lap has too few samples to describe, which happens for
    in-laps, out-laps and any lap where the feed dropped out.
    """
    required = {"session_s", "speed"}
    if telemetry.is_empty() or not required <= set(telemetry.columns):
        return None

    frame = telemetry.sort("session_s").drop_nulls(["session_s", "speed"])
    if len(frame) < MIN_SAMPLES:
        return None

    session_s = frame.get_column("session_s").to_numpy().astype(float)
    speed = frame.get_column("speed").to_numpy().astype(float)
    distance = integrate_distance(speed, session_s)
    if distance[-1] <= 0:
        return None

    grid = np.arange(0.0, distance[-1], grid_step_m)
    if grid.size < 2:
        return None

    def resample(column: str, default: float = 0.0) -> np.ndarray:
        if column not in frame.columns:
            return np.full(grid.size, default)
        values = frame.get_column(column).to_numpy()
        numeric = np.asarray(
            [default if v is None else float(v) for v in values], dtype=float
        )
        return np.asarray(np.interp(grid, distance, numeric), dtype=float)

    return AlignedLap(
        driver_number=driver_number,
        lap_number=lap_number,
        distance_m=grid,
        speed_kmh=np.asarray(np.interp(grid, distance, speed), dtype=float),
        throttle=resample("throttle"),
        brake=resample("brake"),
        gear=resample("gear"),
        elapsed_s=np.asarray(
            np.interp(grid, distance, session_s - session_s[0]), dtype=float
        ),
    )
