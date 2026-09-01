"""Corner detection and per-corner metrics.

Corners are found from the speed trace rather than from a hardcoded circuit map. Every
corner shows the same signature — the car decelerates, reaches a minimum, accelerates
away — so the local minima of a smoothed speed trace *are* the corners.

Deriving them rather than looking them up means the engine works on any circuit,
including ones added to the calendar after it was written, and it adapts to layout
changes without a data update.

What comes out per corner is what a driver comparison actually turns on: where braking
began, the minimum speed carried, and where the throttle came back.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from f1x.engine.telemetry.alignment import AlignedLap

# Smoothing window in metres. Wide enough to ignore sampling noise and small
# corrections, narrow enough to keep two corners of a chicane distinct.
SMOOTHING_WINDOW_M = 25.0

# A dip shallower than this is a kink taken flat, not a corner.
MIN_SPEED_DROP_KMH = 25.0

# Two minima closer than this belong to the same complex.
MIN_CORNER_SEPARATION_M = 150.0

# Below this fraction of the lap's top speed the car is definitely cornering.
CORNER_SPEED_FRACTION = 0.95


@dataclass(frozen=True)
class Corner:
    """One corner, measured from a single lap."""

    index: int
    apex_distance_m: float
    min_speed_kmh: float

    entry_speed_kmh: float
    exit_speed_kmh: float

    # Where the driver first went off throttle or onto the brakes before this apex.
    braking_point_m: float | None
    # Where they picked the throttle back up after it.
    throttle_point_m: float | None

    @property
    def speed_drop_kmh(self) -> float:
        """How much speed the corner scrubs — a proxy for how demanding it is."""
        return self.entry_speed_kmh - self.min_speed_kmh


def _smooth(values: np.ndarray, window_points: int) -> np.ndarray:
    """Moving average that keeps the array length, for local-minimum detection."""
    if window_points < 2 or values.size < window_points:
        return values
    kernel = np.ones(window_points) / window_points
    return np.convolve(values, kernel, mode="same")


def detect_corners(
    lap: AlignedLap,
    *,
    min_speed_drop_kmh: float = MIN_SPEED_DROP_KMH,
    min_separation_m: float = MIN_CORNER_SEPARATION_M,
) -> list[Corner]:
    """Find every corner on a lap and measure it.

    Minima are found on a smoothed trace, then filtered: a corner must scrub real
    speed and must be far enough from the previous one to be a separate feature.
    """
    if lap.distance_m.size < 10:
        return []

    step = float(lap.distance_m[1] - lap.distance_m[0]) if lap.distance_m.size > 1 else 1.0
    window = max(2, int(SMOOTHING_WINDOW_M / step))
    smoothed = _smooth(lap.speed_kmh, window)
    top_speed = float(np.max(lap.speed_kmh))

    # Interior local minima of the smoothed trace.
    candidates = [
        i
        for i in range(1, smoothed.size - 1)
        if smoothed[i] <= smoothed[i - 1]
        and smoothed[i] <= smoothed[i + 1]
        and smoothed[i] < top_speed * CORNER_SPEED_FRACTION
    ]

    corners: list[Corner] = []
    last_distance = -np.inf
    for i in candidates:
        apex_distance = float(lap.distance_m[i])
        if apex_distance - last_distance < min_separation_m:
            continue

        # Entry speed is the local maximum before the apex; exit is the maximum after.
        back = max(0, i - window * 8)
        forward = min(smoothed.size, i + window * 8)
        entry_speed = float(np.max(lap.speed_kmh[back : i + 1]))
        exit_speed = float(np.max(lap.speed_kmh[i:forward]))

        if entry_speed - float(lap.speed_kmh[i]) < min_speed_drop_kmh:
            continue

        corners.append(
            Corner(
                index=len(corners) + 1,
                apex_distance_m=apex_distance,
                min_speed_kmh=float(lap.speed_kmh[i]),
                entry_speed_kmh=entry_speed,
                exit_speed_kmh=exit_speed,
                braking_point_m=_braking_point(lap, back, i),
                throttle_point_m=_throttle_point(lap, i, forward),
            )
        )
        last_distance = apex_distance

    return corners


def _braking_point(lap: AlignedLap, start: int, apex: int) -> float | None:
    """Where the brakes were first applied before this apex."""
    if lap.brake.size == 0 or apex <= start:
        return None
    window = lap.brake[start:apex]
    pressed = np.nonzero(window > 0.5)[0]
    return float(lap.distance_m[start + int(pressed[0])]) if pressed.size else None


def _throttle_point(lap: AlignedLap, apex: int, end: int) -> float | None:
    """Where meaningful throttle resumed after this apex."""
    if lap.throttle.size == 0 or end <= apex:
        return None
    window = lap.throttle[apex:end]
    applied = np.nonzero(window > 50.0)[0]
    return float(lap.distance_m[apex + int(applied[0])]) if applied.size else None


def compare_corners(
    reference: list[Corner], comparison: list[Corner], *, tolerance_m: float = 200.0
) -> list[tuple[Corner, Corner, float]]:
    """Match corners between two laps and report the minimum-speed difference.

    Matched by apex distance within a tolerance, since corner detection can place an
    apex a few metres apart on two laps through the same turn.
    """
    matches: list[tuple[Corner, Corner, float]] = []
    for corner in reference:
        candidates = [
            other
            for other in comparison
            if abs(other.apex_distance_m - corner.apex_distance_m) <= tolerance_m
        ]
        if not candidates:
            continue
        closest = min(
            candidates, key=lambda c: abs(c.apex_distance_m - corner.apex_distance_m)
        )
        matches.append((corner, closest, closest.min_speed_kmh - corner.min_speed_kmh))
    return matches
