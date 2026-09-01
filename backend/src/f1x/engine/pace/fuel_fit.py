"""Fitting the fuel effect across seasons.

``fuel_model`` documents why this cannot be done from one race: ``fuel_load_kg`` is
derived from lap number by a linear burn assumption, so within a single race the two
are perfectly collinear (measured correlation exactly -1.0). A regression on that
returns roughly 1.0 s/kg — thirty times the physical value — because the fuel term
absorbs every effect that trends through a race.

Pooling the same circuit across seasons breaks the collinearity. A circuit run over 57
laps one year and 53 the next has *different fuel loads at the same lap number*, and
different lap numbers at the same fuel load. That decoupling is what makes the mass
term identifiable.

Two things still have to be absorbed or the fuel term will soak them up instead:

- **Season effects.** Cars, tyres and regulations change year to year, so each season
  carries its own baseline pace at every circuit.
- **Tyre age.** Degradation still trends with fuel inside a stint, so it is fitted
  alongside rather than left in the residual.

The result is a per-circuit coefficient where the data supports one, and the published
default everywhere else. A fitted number that fails its plausibility check is discarded,
not applied — the whole point of the exercise is to stop a confident wrong value from
reaching every lap of a race.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import polars as pl

from f1x.engine.pace.fuel_model import (
    DEFAULT_FUEL_EFFECT_S_PER_KG,
    MAX_FUEL_EFFECT,
    MIN_FUEL_EFFECT,
)

# A circuit needs at least this many seasons before the fuel and lap-number effects
# are separable at all.
MIN_SEASONS = 2

# Race lengths must actually differ; two seasons of identical distance reproduce the
# single-season collinearity exactly.
MIN_LAP_COUNT_SPREAD = 1

MIN_LAPS_FOR_FIT = 400


@dataclass(frozen=True)
class CircuitFuelFit:
    """A circuit's fuel sensitivity, fitted across seasons."""

    circuit_key: str
    effect_s_per_kg: float
    n_laps: int
    n_seasons: int
    lap_count_spread: int
    r_squared: float
    fitted: bool
    reason: str = ""

    @property
    def effect(self) -> float:
        """The coefficient to use: the fit when trustworthy, else the published default."""
        return self.effect_s_per_kg if self.fitted else DEFAULT_FUEL_EFFECT_S_PER_KG


def fit_circuit(laps: pl.DataFrame, *, circuit_key: str) -> CircuitFuelFit:
    """Fit one circuit's fuel effect from laps pooled across seasons.

    Design matrix is ``[fuel_load_kg, tyre_age, season dummies, driver-stint dummies]``.
    The dummies absorb baseline pace so the fuel term describes mass rather than the
    difference between one season's cars and another's.
    """
    required = {
        "lap_time_s", "fuel_load_kg", "tyre_life", "stint",
        "driver_number", "season_year", "is_representative",
    }
    if laps.is_empty() or not required <= set(laps.columns):
        return _unfitted(circuit_key, 0, 0, 0, "missing columns")

    usable = laps.filter(
        pl.col("is_representative")
        & pl.col("lap_time_s").is_not_null()
        & pl.col("fuel_load_kg").is_not_null()
        & pl.col("tyre_life").is_not_null()
        & pl.col("stint").is_not_null()
    )
    n_laps = len(usable)
    if n_laps < MIN_LAPS_FOR_FIT:
        return _unfitted(circuit_key, n_laps, 0, 0, "insufficient laps")

    seasons = sorted(set(usable.get_column("season_year").to_list()))
    if len(seasons) < MIN_SEASONS:
        return _unfitted(
            circuit_key, n_laps, len(seasons), 0,
            f"only {len(seasons)} season(s); fuel is collinear with lap number",
        )

    # The identifying variation: race lengths must differ between seasons.
    lap_counts = [
        # Polars aggregates are loosely typed; the schema guarantees an integer here.
        int(
            str(
                usable.filter(pl.col("season_year") == season)
                .get_column("lap_number")
                .max()
                or 0
            )
        )
        for season in seasons
    ]
    spread = max(lap_counts) - min(lap_counts)
    if spread < MIN_LAP_COUNT_SPREAD:
        return _unfitted(
            circuit_key, n_laps, len(seasons), spread,
            "identical race lengths reproduce the single-season collinearity",
        )

    times = usable.get_column("lap_time_s").to_numpy().astype(float)
    fuel = usable.get_column("fuel_load_kg").to_numpy().astype(float)
    age = usable.get_column("tyre_life").to_numpy().astype(float)

    # One column per driver-stint-season: each run gets its own intercept, which also
    # absorbs the season effect since a stint belongs to exactly one season.
    keys = [
        f"{season}:{driver}:{stint}"
        for season, driver, stint in zip(
            usable.get_column("season_year").to_list(),
            usable.get_column("driver_number").to_list(),
            usable.get_column("stint").to_list(),
            strict=True,
        )
    ]
    unique = sorted(set(keys))
    index = {key: i for i, key in enumerate(unique)}
    dummies = np.zeros((n_laps, len(unique)))
    for row, key in enumerate(keys):
        dummies[row, index[key]] = 1.0

    design = np.column_stack([fuel, age, dummies])
    try:
        coefficients, *_ = np.linalg.lstsq(design, times, rcond=None)
    except np.linalg.LinAlgError:
        return _unfitted(circuit_key, n_laps, len(seasons), spread, "singular design")

    effect = float(coefficients[0])
    predicted = design @ coefficients
    variance = float(np.sum((times - times.mean()) ** 2))
    r_squared = (
        1.0 - float(np.sum((times - predicted) ** 2)) / variance if variance > 0 else 0.0
    )

    if not (MIN_FUEL_EFFECT <= effect <= MAX_FUEL_EFFECT):
        return CircuitFuelFit(
            circuit_key=circuit_key,
            effect_s_per_kg=effect,
            n_laps=n_laps,
            n_seasons=len(seasons),
            lap_count_spread=spread,
            r_squared=r_squared,
            fitted=False,
            reason=f"effect {effect:.4f} outside plausible range",
        )

    return CircuitFuelFit(
        circuit_key=circuit_key,
        effect_s_per_kg=effect,
        n_laps=n_laps,
        n_seasons=len(seasons),
        lap_count_spread=spread,
        r_squared=r_squared,
        fitted=True,
    )


def _unfitted(
    circuit: str, laps: int, seasons: int, spread: int, reason: str
) -> CircuitFuelFit:
    return CircuitFuelFit(
        circuit_key=circuit,
        effect_s_per_kg=DEFAULT_FUEL_EFFECT_S_PER_KG,
        n_laps=laps,
        n_seasons=seasons,
        lap_count_spread=spread,
        r_squared=0.0,
        fitted=False,
        reason=reason,
    )
