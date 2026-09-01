"""Compound degradation models.

A stint fit gives one driver's degradation on one set of tyres. Pooling those fits by
compound gives the circuit's tyre behaviour: how much each compound loses per lap, and
how consistently.

Two things are deliberately kept separate. The **median** slope describes the compound;
the **spread** describes how much driver and traffic differences move it. A strategy
model that uses the median without the spread will present a single confident stop lap
where the data supports a window.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import polars as pl

# A compound needs several independent stints before its degradation means anything.
# Below this the figure describes one driver's run, not the tyre.
MIN_STINTS_PER_COMPOUND = 3


@dataclass(frozen=True)
class DegradationCurve:
    """Pooled degradation for one compound at one circuit."""

    session_id: int
    compound: str
    n_stints: int
    n_laps: int

    # Median seconds lost per lap of tyre age. Median, not mean: one damaged car or
    # one driver nursing a problem would drag a mean well off the compound's behaviour.
    degradation_s_per_lap: float
    # Interquartile spread of the per-stint slopes — the honest width of the estimate.
    degradation_iqr_s: float

    # Typical pace on a fresh set, for comparing compounds against each other.
    median_pace_s: float
    # Longest stint anyone completed. An upper bound on observed usable life, not a
    # prediction of where the cliff is.
    max_stint_laps: int

    @property
    def is_physical(self) -> bool:
        """Whether the fitted slope describes tyre wear at all.

        A negative slope means the fuel correction removed more than the real fuel
        effect — common at low-degradation circuits, where the uniform coefficient
        over-corrects. The curve is still reported, so the over-correction is visible
        rather than hidden, but strategy must not treat it as a real gain.
        """
        return self.degradation_s_per_lap >= 0.0

    def loss_after(self, laps: float) -> float:
        """Predicted cumulative time lost after this many laps of tyre age.

        Linear, so it does not model the cliff — a compound that falls away sharply
        past its usable life will be under-predicted. Treat results beyond
        ``max_stint_laps`` as extrapolation. Clamped at zero for the same reason as
        ``degradation_cost``: a tyre never gains time by ageing.
        """
        return max(0.0, self.degradation_s_per_lap) * laps

    @property
    def is_extrapolating_beyond(self) -> int:
        """Tyre age past which predictions are unsupported by observation."""
        return self.max_stint_laps


def build_curves(stint_fits: pl.DataFrame) -> list[DegradationCurve]:
    """Pool reliable stint fits into one curve per compound."""
    if stint_fits.is_empty() or "is_reliable" not in stint_fits.columns:
        return []

    usable = stint_fits.filter(pl.col("is_reliable") & pl.col("compound").is_not_null())
    if usable.is_empty():
        return []

    curves: list[DegradationCurve] = []
    for (session_id, compound), group in usable.group_by(
        ["session_id", "compound"], maintain_order=True
    ):
        if len(group) < MIN_STINTS_PER_COMPOUND:
            continue
        slopes = group.get_column("degradation_s_per_lap").to_numpy()
        curves.append(
            DegradationCurve(
                session_id=int(str(session_id)),
                compound=str(compound),
                n_stints=len(group),
                n_laps=int(str(group.get_column("n_laps").sum())),
                degradation_s_per_lap=float(np.median(slopes)),
                degradation_iqr_s=float(
                    np.percentile(slopes, 75) - np.percentile(slopes, 25)
                ),
                median_pace_s=float(np.median(group.get_column("pace_s").to_numpy())),
                max_stint_laps=int(str(group.get_column("n_laps").max())),
            )
        )
    return curves


def to_frame(curves: list[DegradationCurve]) -> pl.DataFrame:
    """Collect curves into a frame."""
    if not curves:
        return pl.DataFrame(
            schema={
                "session_id": pl.Int32,
                "compound": pl.Utf8,
                "n_stints": pl.Int16,
                "n_laps": pl.Int32,
                "degradation_s_per_lap": pl.Float64,
                "degradation_iqr_s": pl.Float64,
                "median_pace_s": pl.Float64,
                "max_stint_laps": pl.Int16,
            }
        )
    return pl.DataFrame([curve.__dict__ for curve in curves])
