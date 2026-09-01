"""Optimal stop count and stint lengths.

Every strategy trades two costs against each other. Stopping costs the net pit loss.
Not stopping costs accumulated degradation, which grows with every lap on the same set.
The optimum is where one more stop would cost more than the degradation it saves.

For a linear degradation model the shape of the answer is known in advance: with a
fixed slope, equal-length stints minimise total time, because degradation cost is
quadratic in stint length and splitting a long stint unevenly always costs more than
splitting it evenly. That makes the search cheap — enumerate stop counts, and for each
one the best stint split is already determined.

One regulation *is* modelled, because ignoring it produces nonsense. A dry race requires
each driver to use two different dry compounds, so at least one stop is mandatory
regardless of what the arithmetic prefers. Without that floor the optimiser returns
zero stops at low-degradation circuits — internally consistent, and against the rules.

What this does not model: the cliff (degradation is assumed linear to any stint length),
traffic on rejoin, and safety cars. Those turn a clean optimum into a judgement call,
which is why the output is a ranked comparison rather than an instruction.
"""

from __future__ import annotations

from dataclasses import dataclass

# Beyond this the pit loss dominates so heavily that more stops are never competitive,
# and enumerating them wastes time on strategies no team would consider.
MAX_STOPS = 4

# A dry race requires two different dry compounds, so one stop is the regulatory floor.
# Set to 0 only when modelling a wet race, where the requirement does not apply.
MANDATORY_STOPS = 1


@dataclass(frozen=True)
class StrategyOption:
    """One candidate strategy: a stop count and the stint lengths it implies."""

    n_stops: int
    stint_lengths: tuple[int, ...]
    compound: str

    # Total time lost to tyre degradation across the race, relative to running the
    # whole distance on fresh rubber.
    degradation_cost_s: float
    pit_cost_s: float

    @property
    def total_cost_s(self) -> float:
        """Combined cost. Lower is better; the absolute value is not a lap time."""
        return self.degradation_cost_s + self.pit_cost_s


def degradation_cost(stint_laps: int, slope_s_per_lap: float) -> float:
    """Time lost to degradation over one stint.

    Lap n of a stint carries n laps of wear, so the total is the slope times the sum
    of 1..n — quadratic in stint length. That quadratic growth is precisely why
    splitting a long stint pays, and why the pit loss has to be weighed against it.
    """
    if stint_laps <= 0:
        return 0.0
    return slope_s_per_lap * stint_laps * (stint_laps - 1) / 2.0


def split_evenly(total_laps: int, n_stints: int) -> tuple[int, ...]:
    """Divide a race into stints as evenly as the lap count allows."""
    if n_stints <= 0 or total_laps <= 0:
        return ()
    base, remainder = divmod(total_laps, n_stints)
    return tuple(base + (1 if i < remainder else 0) for i in range(n_stints))


def evaluate_strategy(
    *,
    total_laps: int,
    n_stops: int,
    slope_s_per_lap: float,
    net_pit_loss_s: float,
    compound: str = "UNKNOWN",
) -> StrategyOption:
    """Cost one strategy at a given stop count."""
    lengths = split_evenly(total_laps, n_stops + 1)
    return StrategyOption(
        n_stops=n_stops,
        stint_lengths=lengths,
        compound=compound,
        degradation_cost_s=sum(degradation_cost(n, slope_s_per_lap) for n in lengths),
        pit_cost_s=n_stops * net_pit_loss_s,
    )


def optimise(
    *,
    total_laps: int,
    slope_s_per_lap: float,
    net_pit_loss_s: float,
    compound: str = "UNKNOWN",
    max_stops: int = MAX_STOPS,
    min_stops: int = MANDATORY_STOPS,
) -> list[StrategyOption]:
    """Rank legal strategies, cheapest first.

    Starts at ``min_stops`` because a dry race mandates a compound change. Returns
    every option rather than only the winner: the gap between first and second is
    what tells a strategist whether the call is clear or a coin toss.
    """
    if total_laps <= 0:
        return []
    options = [
        evaluate_strategy(
            total_laps=total_laps,
            n_stops=stops,
            slope_s_per_lap=slope_s_per_lap,
            net_pit_loss_s=net_pit_loss_s,
            compound=compound,
        )
        for stops in range(min_stops, max_stops + 1)
    ]
    return sorted(options, key=lambda option: option.total_cost_s)


def optimal_stop_count(
    *,
    total_laps: int,
    slope_s_per_lap: float,
    net_pit_loss_s: float,
    min_stops: int = MANDATORY_STOPS,
) -> int:
    """The legal stop count with the lowest modelled total cost."""
    ranked = optimise(
        total_laps=total_laps,
        slope_s_per_lap=slope_s_per_lap,
        net_pit_loss_s=net_pit_loss_s,
        min_stops=min_stops,
    )
    return ranked[0].n_stops if ranked else min_stops
