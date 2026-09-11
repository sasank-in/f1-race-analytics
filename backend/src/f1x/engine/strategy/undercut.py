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

    The attacker gains, on each lap before the defender responds, the difference
    between a fresh tyre and the defender's current one: the degradation slope times
    how old that tyre is, plus a fixed bonus for warm rubber and clear track.

    **That per-lap difference is not the defender's cumulative loss.** An earlier
    version multiplied ``degradation x tyre_age`` — already the whole deficit a worn
    tyre has accumulated — by the response window, counting the same time twice and
    producing 5-7s gains where a real undercut wins by one or two. It reported 392 of
    480 opportunities as working; if four in five undercuts succeeded, every team
    would pit on every lap they were within three seconds.

    The per-lap advantage is therefore the *marginal* rate: what one further lap on
    old rubber costs the defender relative to a fresh set, which is the slope itself
    plus the out-lap bonus spread across the window.

    Capped below by zero: a fresh tyre is never slower than a worn one, so a negative
    advantage means the inputs disagree, not that pitting loses time on pace.
    """
    # Per lap of the response window, the attacker's fresh tyre is quicker than the
    # defender's by the slope times how much younger it is. The attacker's set is new,
    # so the age difference IS the defender's tyre age — but the advantage accrues one
    # lap at a time, it is not the whole accumulated deficit collected every lap.
    #
    # Over `response_laps` the defender's tyre also keeps ageing, so the edge grows
    # slightly; the mean advantage across the window is what matters.
    ages = [defender_tyre_age + n for n in range(max(response_laps, 1))]
    mean_edge = sum(degradation_s_per_lap * a for a in ages) / len(ages)
    # The fresh-tyre bonus is a one-off out-lap benefit, not a per-lap rate, so it is
    # spread across the window rather than applied to every lap.
    per_lap = mean_edge / max(response_laps, 1) + fresh_tyre_bonus_s / max(response_laps, 1)
    gain_per_lap = max(0.0, per_lap)
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
