"""Cumulative delta time between two laps.

A lap-time gap says one driver was three tenths quicker. It does not say *where*. The
delta trace answers that: at every point on the track, how much time has one driver
gained or lost against the other since the start line.

Read as a shape, it diagnoses the lap. A step down under braking is a later brake
point. A steady climb through a corner is more mid-corner speed. A rise on the straight
after a corner is a better exit — and crucially, that time was won in the corner
before, not on the straight where it shows up.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from f1x.engine.telemetry.alignment import AlignedLap


@dataclass(frozen=True)
class DeltaTrace:
    """Time difference between two laps, sampled by distance."""

    reference_driver: str
    comparison_driver: str
    distance_m: np.ndarray
    # Positive means the comparison lap is behind the reference at that point.
    delta_s: np.ndarray

    @property
    def final_delta_s(self) -> float:
        """Total lap-time difference — the delta at the finish line."""
        return float(self.delta_s[-1]) if self.delta_s.size else 0.0

    @property
    def max_gain_m(self) -> float:
        """Distance at which the comparison lap was furthest ahead."""
        return float(self.distance_m[int(np.argmin(self.delta_s))]) if self.delta_s.size else 0.0

    @property
    def max_loss_m(self) -> float:
        """Distance at which the comparison lap was furthest behind."""
        return float(self.distance_m[int(np.argmax(self.delta_s))]) if self.delta_s.size else 0.0


def compute_delta(reference: AlignedLap, comparison: AlignedLap) -> DeltaTrace | None:
    """Compute the cumulative delta between two aligned laps.

    Both laps must already sit on a distance grid. They are truncated to their common
    distance: two laps rarely measure identically, and extrapolating past the shorter
    one would invent a delta from nothing.
    """
    if reference.distance_m.size == 0 or comparison.distance_m.size == 0:
        return None

    limit = min(reference.lap_distance_m, comparison.lap_distance_m)
    if limit <= 0:
        return None

    grid = reference.distance_m[reference.distance_m <= limit]
    if grid.size < 2:
        return None

    reference_time = np.interp(grid, reference.distance_m, reference.elapsed_s)
    comparison_time = np.interp(grid, comparison.distance_m, comparison.elapsed_s)

    return DeltaTrace(
        reference_driver=reference.driver_number,
        comparison_driver=comparison.driver_number,
        distance_m=grid,
        delta_s=comparison_time - reference_time,
    )


def segment_deltas(trace: DeltaTrace, n_segments: int = 20) -> list[tuple[float, float]]:
    """Break a delta trace into equal segments and report the change across each.

    Useful for summarising where a lap was won without plotting the whole trace:
    each entry is ``(segment_end_distance, time_gained_in_segment)``.
    """
    if trace.distance_m.size < 2 or n_segments <= 0:
        return []
    edges = np.linspace(0, trace.distance_m[-1], n_segments + 1)
    values = np.interp(edges, trace.distance_m, trace.delta_s)
    return [
        (float(edges[i + 1]), float(values[i + 1] - values[i])) for i in range(n_segments)
    ]
