"""Monte Carlo race simulation.

A strategy comparison that returns a single number is misleading. "Two stops is four
seconds faster" sounds decisive until a safety car falls in the wrong place, or an
out-lap lands in traffic, and the four seconds evaporate. What a strategist needs is
the *distribution*: how often does each strategy win, and by how much when it does.

This module runs a race many times with the uncertain quantities resampled each time —
lap-time noise, safety-car timing, pit-stop variation — and reports the spread. A
strategy that wins 51% of the time is a coin toss; one that wins 90% is a decision.

Every input is an estimate from earlier phases: degradation from stint regression, pit
loss from the laps either side of a stop, safety-car probability from race control. The
simulation inherits their limits, so its output is a comparison between strategies under
one consistent set of assumptions, not a prediction of a real race.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

# Lap-time variation a driver shows lap to lap on identical tyres and fuel. Measured
# from 2023 clean laps, where per-driver standard deviation clusters around 0.5 s.
LAP_TIME_NOISE_S = 0.5

# Stationary time varies stop to stop: a wheel that hesitates costs a second or two.
PIT_NOISE_S = 1.0

# A safety car slows every car by the same amount, so it barely moves the *relative*
# standing between strategies — which is the only thing this simulation compares.
# Modelling it as a large absolute penalty (a full 40 % on four laps, roughly 157 s)
# swamps the few seconds that actually separate strategies and makes every race look
# like a coin toss. The neutralised laps are therefore left at racing pace.
#
# What a safety car genuinely changes is the *cost of pitting*: a stop made while the
# field is slowed loses far less track position, because rivals are also circulating
# slowly. That asymmetry is the whole strategic significance of a safety car, and it
# is what the model keeps.
SAFETY_CAR_PIT_DISCOUNT = 0.5

DEFAULT_ITERATIONS = 2_000


@dataclass(frozen=True)
class RaceConditions:
    """Everything the simulation needs about the circuit and the race."""

    total_laps: int
    base_lap_s: float
    net_pit_loss_s: float
    degradation_s_per_lap: float

    # Measured at 0.59 across the 2023 season, not assumed.
    safety_car_probability: float = 0.59
    safety_car_laps: int = 4
    lap_time_noise_s: float = LAP_TIME_NOISE_S
    pit_noise_s: float = PIT_NOISE_S


@dataclass(frozen=True)
class SimulationResult:
    """Distribution of finishing times for one strategy."""

    n_stops: int
    stint_lengths: tuple[int, ...]
    iterations: int

    mean_s: float
    median_s: float
    std_s: float
    p5_s: float
    p95_s: float
    # Share of iterations where a safety car appeared. A sanity check on the sampler,
    # not an output in its own right.
    safety_car_rate: float

    samples: np.ndarray = field(repr=False, default_factory=lambda: np.array([]))

    @property
    def spread_s(self) -> float:
        """Width of the middle 90 % — how much the outcome actually varies."""
        return self.p95_s - self.p5_s


def simulate_strategy(
    conditions: RaceConditions,
    stint_lengths: tuple[int, ...],
    *,
    iterations: int = DEFAULT_ITERATIONS,
    seed: int | None = None,
) -> SimulationResult:
    """Run one strategy many times and return the distribution of race times.

    Each iteration walks the race lap by lap, accumulating degradation within each
    stint and resetting it at every stop, then adds the pit cost. A safety car, when
    one occurs, discounts any stop that falls inside its window — see the note on
    SAFETY_CAR_PIT_DISCOUNT for why it does not also slow the laps themselves.
    """
    rng = np.random.default_rng(seed)
    n_stops = len(stint_lengths) - 1
    total_laps = sum(stint_lengths)
    if total_laps <= 0:
        raise ValueError("stint_lengths must cover at least one lap")

    # Tyre age on each lap: restarts at zero for every stint.
    tyre_age = np.concatenate([np.arange(length) for length in stint_lengths])
    # Clamped for the same reason as the optimiser: a tyre never gains time by ageing.
    slope = max(0.0, conditions.degradation_s_per_lap)
    deterministic = conditions.base_lap_s + slope * tyre_age

    # Lap numbers on which a stop happens (the last lap of each stint but the final one).
    stop_laps = np.cumsum(stint_lengths)[:-1]

    totals = np.empty(iterations)
    had_safety_car = np.zeros(iterations, dtype=bool)

    for i in range(iterations):
        laps = deterministic + rng.normal(0.0, conditions.lap_time_noise_s, total_laps)

        safety_car_window: np.ndarray | None = None
        if rng.random() < conditions.safety_car_probability:
            had_safety_car[i] = True
            # A safety car can start on any lap that leaves room for its full duration.
            latest_start = max(1, total_laps - conditions.safety_car_laps)
            start = int(rng.integers(0, latest_start))
            safety_car_window = np.arange(
                start, min(start + conditions.safety_car_laps, total_laps)
            )

        pit_cost = 0.0
        for stop_lap in stop_laps:
            cost = conditions.net_pit_loss_s + rng.normal(0.0, conditions.pit_noise_s)
            # A stop that lands inside a safety-car window costs much less.
            if safety_car_window is not None and (stop_lap - 1) in safety_car_window:
                cost *= SAFETY_CAR_PIT_DISCOUNT
            pit_cost += cost

        totals[i] = laps.sum() + pit_cost

    return SimulationResult(
        n_stops=n_stops,
        stint_lengths=stint_lengths,
        iterations=iterations,
        mean_s=float(np.mean(totals)),
        median_s=float(np.median(totals)),
        std_s=float(np.std(totals, ddof=1)),
        p5_s=float(np.percentile(totals, 5)),
        p95_s=float(np.percentile(totals, 95)),
        safety_car_rate=float(np.mean(had_safety_car)),
        samples=totals,
    )


@dataclass(frozen=True)
class StrategyComparison:
    """Head-to-head outcome between candidate strategies."""

    results: list[SimulationResult]
    # Share of iterations in which each strategy produced the fastest race time.
    win_rates: dict[int, float]

    @property
    def best(self) -> SimulationResult:
        return min(self.results, key=lambda r: r.median_s)

    @property
    def is_decisive(self) -> bool:
        """Whether the leading strategy wins often enough to call it.

        Below about 65 % the strategies are close enough that a single safety car
        decides the race, and presenting one as correct would overstate the model.
        """
        return max(self.win_rates.values(), default=0.0) >= 0.65


def compare_strategies(
    conditions: RaceConditions,
    candidates: list[tuple[int, ...]],
    *,
    iterations: int = DEFAULT_ITERATIONS,
    seed: int | None = None,
) -> StrategyComparison:
    """Simulate several strategies against each other and count wins.

    All strategies share one random seed so they face the same sampled races. Without
    that, differences between strategies would be confounded with differences in the
    luck each one happened to draw.
    """
    results = [
        simulate_strategy(conditions, stints, iterations=iterations, seed=seed)
        for stints in candidates
    ]
    if not results:
        return StrategyComparison(results=[], win_rates={})

    stacked = np.vstack([result.samples for result in results])
    winners = np.argmin(stacked, axis=0)
    win_rates = {
        results[index].n_stops: float(np.mean(winners == index))
        for index in range(len(results))
    }
    return StrategyComparison(results=results, win_rates=win_rates)
