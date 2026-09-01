"""Pit-lane time loss.

The number strategy needs is not how long a stop took. It is how much time a driver
surrendered by pitting rather than staying out — the *delta* against a hypothetical lap
run at normal pace.

``pit_duration_s`` in ``core.pit_stops`` measures only pit entry to pit exit — the
transit through the pit lane, around 24 s at a typical circuit. That is *not* the cost
of stopping, because the driver would have been covering ground during that time anyway.

The cost shows up in the lap times either side. Measured at 2023 Bahrain: a normal lap
runs about 95 s, the in-lap about 101 s, and the out-lap about 118 s. Comparing those
against the driver's own representative pace gives the time actually surrendered — the
approach this module takes.

Estimated as a low quantile rather than a mean: a mean folds in botched stops, unsafe
releases and stops made under a safety car, none of which describe what a clean stop
costs. Strategy asks what a stop *would* cost, so the answer must come from clean ones.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import polars as pl

# A clean stop sits near the bottom of the distribution; the tail is problems.
CLEAN_STOP_QUANTILE = 0.25

# Physical bounds on pit entry to pit exit. Below this is a timing artefact; above it
# the car was stationary for a repair, a penalty, or a red flag.
MIN_PLAUSIBLE_PIT_S = 15.0
MAX_PLAUSIBLE_PIT_S = 60.0

# Fewer clean stops than this and the estimate describes a handful of events.
MIN_STOPS_FOR_ESTIMATE = 5


@dataclass(frozen=True)
class PitLoss:
    """Time cost of one pit stop at one circuit."""

    session_id: int
    circuit_key: str | None
    n_stops: int

    # Pit entry to pit exit for a clean stop — transit only, not the full cost.
    pit_window_s: float
    # The session's representative green-flag lap, used as the comparison baseline.
    on_track_equivalent_s: float
    # What a stop actually costs: how much the in-lap and out-lap together exceed two
    # normal laps. This is the figure an undercut calculation must beat.
    net_loss_s: float

    # Spread of clean stops, so a strategy model can carry the uncertainty.
    spread_s: float

    @property
    def is_reliable(self) -> bool:
        return self.n_stops >= MIN_STOPS_FOR_ESTIMATE and self.net_loss_s > 0


def estimate_pit_loss(
    pit_stops: pl.DataFrame,
    laps: pl.DataFrame,
    *,
    reference_lap_s: float,
    session_id: int,
    circuit_key: str | None = None,
) -> PitLoss | None:
    """Estimate the net cost of a pit stop from the laps either side of it.

    For each stop, the excess is ``(in-lap + out-lap) - 2 * reference_lap``: the time
    those two laps cost beyond two normal ones. That captures the whole penalty —
    slowing for the pit entry, the transit itself, and rejoining on cold tyres.

    ``reference_lap_s`` is the session's representative green-flag lap time.
    """
    if pit_stops.is_empty() or laps.is_empty():
        return None
    if not {"lap_number", "driver_number", "lap_time_s"} <= set(laps.columns):
        return None

    lap_lookup = {
        (str(row["driver_number"]), int(row["lap_number"])): row["lap_time_s"]
        for row in laps.to_dicts()
        if row.get("lap_time_s") is not None
    }

    excesses: list[float] = []
    windows: list[float] = []
    for stop in pit_stops.to_dicts():
        driver = str(stop.get("driver_number"))
        lap_number = stop.get("lap_number")
        duration = stop.get("pit_duration_s")
        if lap_number is None:
            continue
        in_lap = lap_lookup.get((driver, int(lap_number)))
        out_lap = lap_lookup.get((driver, int(lap_number) + 1))
        if in_lap is None or out_lap is None:
            continue
        excess = float(in_lap) + float(out_lap) - 2.0 * reference_lap_s
        # Reject stops made under a safety car or with a problem: those laps are
        # slow for reasons unrelated to the stop itself.
        if MIN_PLAUSIBLE_PIT_S <= excess <= MAX_PLAUSIBLE_PIT_S:
            excesses.append(excess)
            if duration is not None:
                windows.append(float(duration))

    if len(excesses) < MIN_STOPS_FOR_ESTIMATE:
        return None

    values = np.array(excesses)
    return PitLoss(
        session_id=session_id,
        circuit_key=circuit_key,
        n_stops=len(excesses),
        pit_window_s=float(np.quantile(windows, CLEAN_STOP_QUANTILE)) if windows else 0.0,
        on_track_equivalent_s=reference_lap_s,
        net_loss_s=float(np.quantile(values, CLEAN_STOP_QUANTILE)),
        spread_s=float(np.percentile(values, 75) - np.percentile(values, 25)),
    )


def estimate_from_laps(
    pit_stops: pl.DataFrame,
    laps: pl.DataFrame,
    *,
    session_id: int,
    circuit_key: str | None = None,
) -> PitLoss | None:
    """Estimate pit loss using the session's own representative pace as reference."""
    if laps.is_empty() or "lap_time_s" not in laps.columns:
        return None
    green = laps.filter(pl.col("is_representative") & pl.col("lap_time_s").is_not_null())
    if green.is_empty():
        return None
    reference = float(np.median(green.get_column("lap_time_s").to_numpy()))
    return estimate_pit_loss(
        pit_stops,
        laps,
        reference_lap_s=reference,
        session_id=session_id,
        circuit_key=circuit_key,
    )
