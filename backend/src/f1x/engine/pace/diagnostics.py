"""Diagnostics for the fuel correction.

The fuel coefficient is not directly observable, so its quality has to be judged by
what it leaves behind. Two residual signatures tell you whether a correction is right:

**Trend.** After correcting, lap time should have no systematic relationship with lap
number. A residual negative correlation means the correction is too weak — fuel burn is
still showing through. A positive one means it is too strong.

**Degradation sign.** A correction that over-shoots pushes fitted stint slopes negative,
which reads as tyres getting faster with age. That is physically impossible, so any
negative slope is direct evidence the coefficient at that circuit is too large.

The second is the sharper test, because it fails loudly rather than degrading quietly,
and it is what surfaced the problem at Jeddah, Melbourne and Baku.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import polars as pl


@dataclass(frozen=True)
class CorrectionQuality:
    """How well the fuel correction performed on one session."""

    session_id: int
    circuit_key: str | None
    n_laps: int

    # Correlation of lap time against lap number, before and after correcting.
    raw_trend: float
    corrected_trend: float

    @property
    def improvement(self) -> float:
        """How much of the burn-off trend the correction removed, as a fraction.

        1.0 means the trend was eliminated; 0.0 that nothing changed; negative that
        the correction made matters worse.
        """
        if self.raw_trend == 0.0:
            return 0.0
        return 1.0 - abs(self.corrected_trend) / abs(self.raw_trend)

    @property
    def is_overcorrected(self) -> bool:
        """Whether the correction pushed the trend past zero into positive territory.

        A residual positive trend means lap times appear to get *slower* as fuel
        burns off, which only happens when too much has been subtracted.

        The threshold is deliberately well above zero. Lap-to-lap noise correlates
        weakly with lap number by chance, so even a perfect correction leaves a small
        positive residual — measured at +0.18 on a 60-lap sample with 0.3 s of
        scatter. Flagging that would report over-correction on a correction that is
        exactly right.
        """
        return self.raw_trend < -0.1 and self.corrected_trend > 0.30

    @property
    def verdict(self) -> str:
        if self.is_overcorrected:
            return "over-corrected"
        if abs(self.corrected_trend) < 0.25:
            return "good"
        if self.improvement > 0.5:
            return "improved"
        return "weak"


def assess_correction(
    laps: pl.DataFrame, *, session_id: int, circuit_key: str | None = None
) -> CorrectionQuality | None:
    """Measure the residual trend before and after fuel correction."""
    required = {"lap_number", "lap_time_s", "fuel_corrected_s", "is_representative"}
    if laps.is_empty() or not required <= set(laps.columns):
        return None

    usable = laps.filter(
        pl.col("is_representative")
        & pl.col("lap_time_s").is_not_null()
        & pl.col("fuel_corrected_s").is_not_null()
    )
    if len(usable) < 50:
        return None

    lap_number = usable.get_column("lap_number").to_numpy().astype(float)
    raw = usable.get_column("lap_time_s").to_numpy().astype(float)
    corrected = usable.get_column("fuel_corrected_s").to_numpy().astype(float)

    def correlation(x: np.ndarray, y: np.ndarray) -> float:
        if x.std() == 0 or y.std() == 0:
            return 0.0
        return float(np.corrcoef(x, y)[0, 1])

    return CorrectionQuality(
        session_id=session_id,
        circuit_key=circuit_key,
        n_laps=len(usable),
        raw_trend=correlation(lap_number, raw),
        corrected_trend=correlation(lap_number, corrected),
    )


@dataclass(frozen=True)
class DegradationHealth:
    """Whether fitted degradation slopes are physically possible."""

    n_curves: int
    n_negative: int

    @property
    def negative_fraction(self) -> float:
        return self.n_negative / self.n_curves if self.n_curves else 0.0

    @property
    def is_healthy(self) -> bool:
        """No negative slopes at all.

        Not a threshold: a single negative slope means the correction is wrong
        somewhere, and averaging that away would hide it.
        """
        return self.n_negative == 0


def assess_degradation(curves: pl.DataFrame) -> DegradationHealth:
    """Count degradation curves whose slope is physically impossible."""
    if curves.is_empty() or "degradation_s_per_lap" not in curves.columns:
        return DegradationHealth(n_curves=0, n_negative=0)
    slopes = curves.get_column("degradation_s_per_lap").drop_nulls()
    return DegradationHealth(
        n_curves=len(slopes), n_negative=int((slopes < 0).sum())
    )
