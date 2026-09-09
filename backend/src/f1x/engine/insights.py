"""What was notable about a race.

Every other module answers a question the reader already had. This one decides which
question is worth asking about a particular race — the difference between a page of
correct numbers and a page that tells you something.

A finding earns its place only if it is *unusual*. "The winner was quickest" is true of
most races and worth one line; "the third-quickest car retired" is worth opening the
race for. So each rule below carries a threshold, and stays silent when the race is
ordinary rather than manufacturing drama from noise.

Pure functions over already-loaded rows: no database, no HTTP. The thresholds are
judgements and are stated as named constants so they can be argued with.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol


class PaceRow(Protocol):
    """The subset of a pace ranking these rules read."""

    driver_number: str
    abbreviation: str | None
    rank: int
    gap_to_best_s: float
    finish_position: float | None
    did_not_finish: bool
    std_s: float


#: A win by more than this on corrected pace is dominance rather than a close race.
#: Roughly the gap between a front-running car and the next team on a normal weekend.
DOMINANT_GAP_S = 0.35

#: Below this the front two were on the same pace and the result turned on something
#: other than the car.
CLOSE_FIELD_S = 0.10

#: Retirements beyond this make attrition part of the story rather than incidental.
HIGH_ATTRITION = 4

#: A degradation slope past this is a tyre-limited race where strategy dominates.
HIGH_DEGRADATION_S = 0.12

#: A pace ranking this far from the finishing position is a race the result misreports.
BIG_PLACE_SWING = 4


@dataclass(frozen=True)
class Insight:
    """One finding, with the number it came from."""

    kind: str
    headline: str
    detail: str | None = None
    magnitude: float | None = None


def _name(row: PaceRow) -> str:
    """Prefer the driver code; fall back to the car number when the entry is unknown."""
    return str(row.abbreviation) if row.abbreviation else f"car {row.driver_number}"


def _places(value: float | None) -> str:
    return "place" if value == 1 else "places"


def build_insights(
    pace: Sequence[PaceRow],
    *,
    winner_number: str | None = None,
    n_retirements: int = 0,
    degradation_s_per_lap: float | None = None,
    worst_compound: str | None = None,
    net_pit_loss_s: float | None = None,
    optimal_stops: int | None = None,
) -> list[Insight]:
    """Findings for one race, most notable first.

    ``pace`` is the corrected pace ranking, already ordered. Everything else is
    optional: a race analysed without strategy still yields pace findings rather than
    an empty list.
    """
    if not pace:
        return []

    insights: list[Insight] = []
    quickest = pace[0]

    # --- pace against result, which is the whole premise ------------------
    if winner_number and quickest.driver_number != winner_number:
        winner_row = next(
            (row for row in pace if row.driver_number == winner_number), None
        )
        detail = None
        if winner_row is not None:
            detail = (
                f"{_name(winner_row)} won from P{winner_row.rank} on pace, "
                f"{winner_row.gap_to_best_s:+.3f}s off the quickest car."
            )
        insights.append(
            Insight(
                kind="upset",
                headline=f"{_name(quickest)} had the quickest car and did not win",
                detail=detail,
                magnitude=(winner_row.gap_to_best_s if winner_row else None),
            )
        )

    # --- a quick car that finished nowhere --------------------------------
    for row in pace:
        if row.did_not_finish and row.rank <= 3:
            insights.append(
                Insight(
                    kind="attrition",
                    headline=f"{_name(row)} was P{row.rank} on pace and retired",
                    detail="Pace is measured from the laps actually completed, so a "
                    "retirement does not make the car slow — it makes the result "
                    "a poor guide to it.",
                    magnitude=float(row.rank),
                )
            )
            break

    # --- a car whose finish badly misreports its pace ---------------------
    swings = [
        (row, int(row.finish_position) - row.rank)
        for row in pace
        if row.finish_position is not None and not row.did_not_finish
    ]
    worst = max(swings, key=lambda pair: pair[1], default=None)
    if worst and worst[1] >= BIG_PLACE_SWING:
        row, swing = worst
        # Bound to a local: the comprehension above guarantees this is not None, but
        # the narrowing does not survive into the f-string.
        finished = int(row.finish_position or 0)
        insights.append(
            Insight(
                kind="upset",
                headline=(
                    f"{_name(row)} finished P{finished} with the P{row.rank} car"
                ),
                detail=f"{swing} {_places(swing)} worse than the pace supported — "
                "a pit-lane start, damage, or a race spent in traffic.",
                magnitude=float(swing),
            )
        )

    # --- how close the front was ------------------------------------------
    if len(pace) >= 2:
        margin = pace[1].gap_to_best_s
        if margin >= DOMINANT_GAP_S:
            insights.append(
                Insight(
                    kind="dominance",
                    headline=f"{_name(quickest)} was {margin:.3f}s clear on pace",
                    detail=f"The next car, {_name(pace[1])}, could not run with it.",
                    magnitude=margin,
                )
            )
        elif margin <= CLOSE_FIELD_S:
            insights.append(
                Insight(
                    kind="close_field",
                    headline=(
                        f"{_name(quickest)} and {_name(pace[1])} were separated by "
                        f"{margin:.3f}s"
                    ),
                    detail="Close enough that track position and strategy decided it, "
                    "not the car.",
                    magnitude=margin,
                )
            )

    # --- tyres ------------------------------------------------------------
    if degradation_s_per_lap is not None and degradation_s_per_lap >= HIGH_DEGRADATION_S:
        compound = f" on the {worst_compound}" if worst_compound else ""
        insights.append(
            Insight(
                kind="degradation",
                headline=(
                    f"Tyres fell away at {degradation_s_per_lap:.3f}s per lap{compound}"
                ),
                detail="High enough that stint length, not raw pace, set the order. "
                "Fitted from lap times rather than measured.",
                magnitude=degradation_s_per_lap,
            )
        )

    # --- strategy ---------------------------------------------------------
    # Pit loss is optional: it is computed on demand rather than stored, so the
    # finding still stands on the stop count when it is absent.
    if optimal_stops is not None:
        cost = f", at {net_pit_loss_s:.1f}s a stop" if net_pit_loss_s is not None else ""
        insights.append(
            Insight(
                kind="strategy",
                headline=f"The field ran a {optimal_stops}-stop race{cost}",
                detail="The typical stop count actually made, not the optimiser's "
                "recommendation.",
                magnitude=float(optimal_stops),
            )
        )

    # --- attrition --------------------------------------------------------
    if n_retirements >= HIGH_ATTRITION:
        insights.append(
            Insight(
                kind="attrition",
                headline=f"{n_retirements} cars did not finish",
                detail="Enough that finishing position reflects reliability and "
                "incident as much as pace.",
                magnitude=float(n_retirements),
            )
        )

    return insights
