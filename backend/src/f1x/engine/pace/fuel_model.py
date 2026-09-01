"""Fitting the fuel effect per circuit instead of assuming it.

The transform layer applies a uniform 0.030 s/kg because it has no better number
available at ingest time. That constant is a published rule of thumb, and using it
everywhere over-corrects circuits where mass matters less — two of the 22 races in
2023 end up with a *positive* lap-time trend after correction, which is the signature
of a correction that is too strong.

This module recovers the real coefficient from the data. Within a single stint on one
compound, lap time moves for two reasons: fuel burning off (making the car faster) and
tyres wearing (making it slower). Regressing raw lap time on both fuel load and tyre
age at once separates them, and the fuel term is the coefficient we want.

**What this can and cannot recover.** ``fuel_load_kg`` is derived from lap number by a
linear burn assumption, so within one race it is perfectly collinear with lap number
(measured: correlation exactly -1.0). A regression on it therefore cannot isolate mass;
it absorbs every effect that trends monotonically through a race — track evolution,
rubber build-in, ambient cooling — and returns a coefficient around 1.0 s/kg, roughly
thirty times the physical value.

That is an identification problem, not a numerical one, and no amount of fitting fixes
it from a single race. What *is* separable is the combined per-lap trend after tyre age
is accounted for. This module therefore reports that combined trend honestly rather
than mislabelling it as a fuel coefficient, and only promotes it to a fuel effect when
it survives a physical plausibility check.

Genuinely isolating mass needs a source of fuel variation independent of race
progress — comparing the same circuit across seasons with different race lengths, or
using practice runs where teams deliberately vary fuel load. Both are future work; the
default coefficient is used until then.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import polars as pl

# The published rule of thumb, used when a fit is not trustworthy.
DEFAULT_FUEL_EFFECT_S_PER_KG = 0.030

# Physically credible bounds. A car cannot gain time from carrying fuel, and no
# circuit costs more than a tenth per kilogram; a fit outside this range means the
# regression latched onto something other than mass.
MIN_FUEL_EFFECT = 0.005
MAX_FUEL_EFFECT = 0.100

# Below this, pooled across the race, the fit is noise.
MIN_LAPS_FOR_FIT = 200
MIN_STINTS_FOR_FIT = 8


@dataclass(frozen=True)
class FuelFit:
    """A circuit's fitted fuel sensitivity."""

    circuit_key: str
    session_id: int
    effect_s_per_kg: float
    n_laps: int
    n_stints: int
    r_squared: float
    fitted: bool
    reason: str = ""

    @property
    def effect(self) -> float:
        """The coefficient to actually use — the fit when trustworthy, else the default."""
        return self.effect_s_per_kg if self.fitted else DEFAULT_FUEL_EFFECT_S_PER_KG


def fit_fuel_effect(laps: pl.DataFrame, *, circuit_key: str, session_id: int) -> FuelFit:
    """Estimate seconds-per-kilogram for one race.

    Design matrix is ``[fuel_load_kg, tyre_age, stint dummies]``. The stint dummies
    absorb each run's own baseline pace, so the remaining terms describe within-stint
    behaviour rather than differences between stints.

    The fuel coefficient this recovers is a *combined* per-lap trend, not mass alone —
    see the module docstring. It is reported with ``fitted=False`` and a reason
    whenever it falls outside the physically plausible range, which on single-race
    data is essentially always. Callers get the default coefficient in that case,
    which is the correct outcome: a wrong number confidently applied to every lap of
    a race is worse than a rule of thumb.
    """
    required = {"lap_time_s", "fuel_load_kg", "tyre_life", "stint", "is_representative"}
    if laps.is_empty() or not required <= set(laps.columns):
        return _unfitted(circuit_key, session_id, 0, 0, "missing columns")

    usable = laps.filter(
        pl.col("is_representative")
        & pl.col("lap_time_s").is_not_null()
        & pl.col("fuel_load_kg").is_not_null()
        & pl.col("tyre_life").is_not_null()
        & pl.col("stint").is_not_null()
    )
    n_laps = len(usable)
    stint_ids = usable.select(["driver_number", "stint"]).unique() if n_laps else None
    n_stints = len(stint_ids) if stint_ids is not None else 0

    if n_laps < MIN_LAPS_FOR_FIT or n_stints < MIN_STINTS_FOR_FIT:
        return _unfitted(circuit_key, session_id, n_laps, n_stints, "insufficient data")

    times = usable.get_column("lap_time_s").to_numpy()
    fuel = usable.get_column("fuel_load_kg").to_numpy()
    age = usable.get_column("tyre_life").to_numpy()

    # One dummy per driver-stint so each run carries its own baseline pace.
    keys = [
        f"{d}:{s}"
        for d, s in zip(
            usable.get_column("driver_number").to_list(),
            usable.get_column("stint").to_list(),
            strict=True,
        )
    ]
    unique_keys = sorted(set(keys))
    dummies = np.zeros((n_laps, len(unique_keys)))
    index = {key: i for i, key in enumerate(unique_keys)}
    for row, key in enumerate(keys):
        dummies[row, index[key]] = 1.0

    design = np.column_stack([fuel, age, dummies])
    try:
        coefficients, *_ = np.linalg.lstsq(design, times, rcond=None)
    except np.linalg.LinAlgError:
        return _unfitted(circuit_key, session_id, n_laps, n_stints, "singular design")

    effect = float(coefficients[0])
    predicted = design @ coefficients
    total_variance = float(np.sum((times - times.mean()) ** 2))
    r_squared = (
        1.0 - float(np.sum((times - predicted) ** 2)) / total_variance
        if total_variance > 0
        else 0.0
    )

    if not (MIN_FUEL_EFFECT <= effect <= MAX_FUEL_EFFECT):
        return FuelFit(
            circuit_key=circuit_key,
            session_id=session_id,
            effect_s_per_kg=effect,
            n_laps=n_laps,
            n_stints=n_stints,
            r_squared=r_squared,
            fitted=False,
            reason=f"effect {effect:.4f} outside plausible range",
        )

    return FuelFit(
        circuit_key=circuit_key,
        session_id=session_id,
        effect_s_per_kg=effect,
        n_laps=n_laps,
        n_stints=n_stints,
        r_squared=r_squared,
        fitted=True,
    )


def _unfitted(circuit: str, session_id: int, laps: int, stints: int, reason: str) -> FuelFit:
    return FuelFit(
        circuit_key=circuit,
        session_id=session_id,
        effect_s_per_kg=DEFAULT_FUEL_EFFECT_S_PER_KG,
        n_laps=laps,
        n_stints=stints,
        r_squared=0.0,
        fitted=False,
        reason=reason,
    )
