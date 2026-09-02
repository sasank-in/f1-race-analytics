"""Teammate comparison.

The hardest thing about judging a Formula 1 driver is that most of what you see is the
car. A driver in a quick car looks quick. The one comparison that removes the car is
against the person in the other one: same machinery, same upgrades, same strategy
department, same weekend.

That makes the teammate delta the closest thing the sport has to a controlled
experiment — and the reason it is quoted so often in paddock analysis.

What it still does not control for: strategy calls that split the garage, damage,
different tyre allocations, and one driver spending a race in traffic while the other
runs free. So the delta is reported with the sample it came from, and a pairing thin
enough that one bad afternoon would move it is flagged rather than presented as fact.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import polars as pl

# Below this the comparison describes a handful of races rather than a season.
MIN_SHARED_SESSIONS = 3

# A gap this large is not a driver difference. It is a car that broke, a strategy that
# collapsed, or a session one of them barely ran.
MAX_PLAUSIBLE_DELTA_S = 3.0


@dataclass(frozen=True)
class TeammateDelta:
    """One pairing's head-to-head over the sessions they both completed."""

    team_key: str | None
    driver_a: str
    driver_b: str
    n_sessions: int

    # Mean pace difference, seconds per lap. Negative means driver A was quicker.
    mean_delta_s: float
    median_delta_s: float
    # Spread across sessions: a small mean with a large spread is not a settled result.
    std_delta_s: float

    sessions_a_ahead: int
    sessions_b_ahead: int

    @property
    def faster_driver(self) -> str:
        return self.driver_a if self.median_delta_s < 0 else self.driver_b

    @property
    def margin_s(self) -> float:
        """Absolute gap, so the headline reads the same whichever way round it is."""
        return abs(self.median_delta_s)

    @property
    def is_reliable(self) -> bool:
        """Whether the pairing has enough races to mean anything."""
        return (
            self.n_sessions >= MIN_SHARED_SESSIONS
            and self.margin_s <= MAX_PLAUSIBLE_DELTA_S
        )

    @property
    def is_decisive(self) -> bool:
        """Whether one driver was consistently ahead rather than on average ahead.

        A 0.05 s mean built from wild swings says less than a 0.05 s mean where the
        same driver won fifteen weekends out of eighteen. The head-to-head count is
        the part that distinguishes them.
        """
        total = self.sessions_a_ahead + self.sessions_b_ahead
        if total == 0:
            return False
        return max(self.sessions_a_ahead, self.sessions_b_ahead) / total >= 0.7


def compare_teammates(pace: pl.DataFrame, entries: pl.DataFrame) -> list[TeammateDelta]:
    """Build head-to-head deltas for every pairing that shared a car.

    ``pace`` is mart.pace_rankings; ``entries`` maps each session's drivers to their
    team, which is what defines a pairing. Team membership is taken per session rather
    than per season, so a mid-season driver change produces two pairings rather than
    one incoherent average.
    """
    required_pace = {"session_id", "driver_number", "pace_s"}
    required_entries = {"session_id", "driver_number", "team_key"}
    if pace.is_empty() or entries.is_empty():
        return []
    if not required_pace <= set(pace.columns) or not required_entries <= set(entries.columns):
        return []

    joined = pace.join(entries, on=["session_id", "driver_number"], how="inner")
    if joined.is_empty():
        return []

    results: list[TeammateDelta] = []
    for (team,), group in joined.group_by(["team_key"], maintain_order=True):
        drivers = sorted(set(group.get_column("driver_number").to_list()))
        # A team fields two cars; more than that means a mid-season replacement, so
        # every pairing that actually shared sessions is compared separately.
        for i, driver_a in enumerate(drivers):
            for driver_b in drivers[i + 1 :]:
                delta = _pairing(group, str(team) if team else None, driver_a, driver_b)
                if delta is not None:
                    results.append(delta)

    return sorted(results, key=lambda d: -d.n_sessions)


def _pairing(
    group: pl.DataFrame, team: str | None, driver_a: str, driver_b: str
) -> TeammateDelta | None:
    """Compare two drivers over the sessions they both have a pace figure for."""
    a = group.filter(pl.col("driver_number") == driver_a).select(["session_id", "pace_s"])
    b = group.filter(pl.col("driver_number") == driver_b).select(["session_id", "pace_s"])

    # Inner join on session: a race only one of them finished says nothing about
    # which is quicker, so it is dropped rather than filled.
    shared = a.join(b, on="session_id", how="inner", suffix="_b")
    if len(shared) < 1:
        return None

    deltas = (
        shared.get_column("pace_s").to_numpy() - shared.get_column("pace_s_b").to_numpy()
    )
    deltas = deltas[np.isfinite(deltas)]
    if deltas.size == 0:
        return None

    return TeammateDelta(
        team_key=team,
        driver_a=driver_a,
        driver_b=driver_b,
        n_sessions=int(deltas.size),
        mean_delta_s=float(np.mean(deltas)),
        median_delta_s=float(np.median(deltas)),
        std_delta_s=float(np.std(deltas, ddof=1)) if deltas.size > 1 else 0.0,
        sessions_a_ahead=int(np.sum(deltas < 0)),
        sessions_b_ahead=int(np.sum(deltas > 0)),
    )
