"""Undercut and overcut windows.

The undercut is the sharpest question in race strategy. A driver stuck behind a rival
pits first, gets fresh tyres, and runs a fast out-lap while the rival is still on worn
ones. If the time gained exceeds the gap, they emerge ahead.

The arithmetic is a race between two quantities. Pitting costs the net pit loss and
gains the difference between fresh-tyre pace and the rival's degraded pace, compounded
over the laps before the rival responds. The undercut works when:

    gain_per_lap * laps_of_advantage  >  current_gap

The overcut is the mirror image: staying out while a rival pits, betting that clear
track and a still-working tyre beat their out-lap on cold rubber. It pays where
degradation is low and out-laps are slow — cold or abrasive circuits.

Everything here is a *model*. It assumes both drivers hit their expected pace, that
traffic on exit is neutral, and that no safety car intervenes. Real races violate all
three. The output is a window, not a prediction.
"""

from __future__ import annotations

from dataclasses import dataclass

import polars as pl

# Laps of fresh-tyre advantage before the rival has responded and equalised. One lap
# to react plus one to complete their own stop.
DEFAULT_RESPONSE_LAPS = 2

# A fresh set is worth roughly this much over a tyre at the end of its useful life,
# beyond what the degradation slope alone predicts — the out-lap benefit of warm
# rubber and clear track. Conservative; circuits vary widely.
FRESH_TYRE_BONUS_S = 0.5


@dataclass(frozen=True)
class UndercutWindow:
    """Whether an undercut on a given lap would have worked."""

    session_id: int
    attacker: str
    defender: str
    lap_number: int

    gap_s: float
    # Per-lap pace advantage a fresh set gives over the rival's current tyres.
    gain_per_lap_s: float
    # Total advantage across the laps before the rival responds.
    total_gain_s: float
    net_pit_loss_s: float

    @property
    def undercut_works(self) -> bool:
        """True when pitting now would emerge ahead of the rival."""
        return self.total_gain_s > self.gap_s

    @property
    def margin_s(self) -> float:
        """How much the undercut wins or misses by. Negative means it fails."""
        return self.total_gain_s - self.gap_s

    @property
    def verdict(self) -> str:
        if self.margin_s > 0.5:
            return "undercut"
        if self.margin_s < -0.5:
            return "hold"
        return "marginal"


def evaluate_undercut(
    *,
    session_id: int,
    attacker: str,
    defender: str,
    lap_number: int,
    gap_s: float,
    defender_tyre_age: float,
    degradation_s_per_lap: float,
    net_pit_loss_s: float,
    response_laps: int = DEFAULT_RESPONSE_LAPS,
    fresh_tyre_bonus_s: float = FRESH_TYRE_BONUS_S,
) -> UndercutWindow:
    """Evaluate one undercut opportunity.

    The advantage comes from the degradation the defender has already accumulated,
    plus a fixed bonus for fresh rubber. It is capped below by zero: a fresh tyre is
    never *slower* than a worn one, so a negative advantage means the model's inputs
    disagree, not that pitting would lose time on pace.
    """
    gain_per_lap = max(0.0, degradation_s_per_lap * defender_tyre_age + fresh_tyre_bonus_s)
    return UndercutWindow(
        session_id=session_id,
        attacker=attacker,
        defender=defender,
        lap_number=lap_number,
        gap_s=gap_s,
        gain_per_lap_s=gain_per_lap,
        total_gain_s=gain_per_lap * response_laps,
        net_pit_loss_s=net_pit_loss_s,
    )


def scan_session(
    laps: pl.DataFrame,
    *,
    session_id: int,
    degradation_s_per_lap: float,
    net_pit_loss_s: float,
    max_gap_s: float = 3.0,
) -> list[UndercutWindow]:
    """Find every lap where one driver was close enough behind another to try an undercut.

    Only pairs within ``max_gap_s`` are considered. Beyond that the undercut is not a
    live option and enumerating it produces noise rather than insight.
    """
    required = {"lap_number", "position", "gap_ahead_s", "driver_number", "tyre_life"}
    if laps.is_empty() or not required <= set(laps.columns):
        return []

    # Identify the defender BEFORE filtering: the car ahead is the previous row in
    # position order, and filtering first would remove the leader and break adjacency.
    ordered = laps.filter(
        pl.col("position").is_not_null() & pl.col("tyre_life").is_not_null()
    ).sort(["lap_number", "position"])
    if ordered.is_empty():
        return []

    by_lap = ordered.with_columns(
        defender=pl.col("driver_number").shift(1).over("lap_number"),
        defender_age=pl.col("tyre_life").shift(1).over("lap_number"),
    ).filter(
        pl.col("defender").is_not_null()
        & pl.col("gap_ahead_s").is_not_null()
        & (pl.col("gap_ahead_s") <= max_gap_s)
        & (pl.col("gap_ahead_s") > 0)
    )

    return [
        evaluate_undercut(
            session_id=session_id,
            attacker=str(row["driver_number"]),
            defender=str(row["defender"]),
            lap_number=int(row["lap_number"]),
            gap_s=float(row["gap_ahead_s"]),
            defender_tyre_age=float(row["defender_age"]),
            degradation_s_per_lap=degradation_s_per_lap,
            net_pit_loss_s=net_pit_loss_s,
        )
        for row in by_lap.to_dicts()
    ]


def to_frame(windows: list[UndercutWindow]) -> pl.DataFrame:
    """Collect undercut windows into a frame."""
    if not windows:
        return pl.DataFrame(
            schema={
                "session_id": pl.Int32,
                "attacker": pl.Utf8,
                "defender": pl.Utf8,
                "lap_number": pl.Int16,
                "gap_s": pl.Float64,
                "gain_per_lap_s": pl.Float64,
                "total_gain_s": pl.Float64,
                "net_pit_loss_s": pl.Float64,
                "margin_s": pl.Float64,
                "verdict": pl.Utf8,
            }
        )
    return pl.DataFrame(
        [
            {**w.__dict__, "margin_s": w.margin_s, "verdict": w.verdict}
            for w in windows
        ]
    )
