"""Winner, podium and what was notable about a race.

The other analysis endpoints each answer one question. This one answers "what should I
look at here", which is the question a reader actually arrives with — and which every
other endpoint leaves them to work out from a table.

The findings themselves come from `engine.insights`, a pure function. This module only
gathers the rows it needs.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from sqlalchemy import text

from f1x.api.deps import ResponseCache, get_cache, get_engine
from f1x.api.schemas import (
    InsightOut,
    Meta,
    PodiumEntryOut,
    RaceInsightsResponse,
)
from f1x.config import ENGINE_VERSION
from f1x.engine.insights import build_insights

router = APIRouter(tags=["analysis"])


# Classified finishers with their pace ranking alongside. The left join to pace is
# deliberate: a driver who retired early may have no ranking, and should still appear
# in the result order rather than vanish from the podium.
RESULTS_QUERY = """
    SELECT r.position, en.driver_number, d.abbreviation, t.name AS team,
           r.status, p.rank AS pace_rank, p.gap_to_best_s
    FROM core.results r
    JOIN core.entries en
      ON en.session_id = r.session_id AND en.driver_id = r.driver_id
    LEFT JOIN core.drivers d ON d.id = r.driver_id
    -- Team comes from the entry, not the result: core.results.team_id is not
    -- populated by the ingest, while core.entries carries it for every car.
    LEFT JOIN core.teams t ON t.id = en.team_id
    LEFT JOIN mart.pace_rankings p
           ON p.session_id = r.session_id
          AND p.driver_number = en.driver_number
          AND p.engine_version = :v
    WHERE r.session_id = :s AND r.position IS NOT NULL
    ORDER BY r.position
"""

PACE_QUERY = """
    SELECT p.driver_number, d.abbreviation, p.rank, p.gap_to_best_s, p.std_s,
           r.position AS finish_position,
           coalesce(r.status IN ('Retired', 'Withdrew', 'Disqualified'), false)
             AS did_not_finish
    FROM mart.pace_rankings p
    LEFT JOIN core.entries en
           ON en.session_id = p.session_id AND en.driver_number = p.driver_number
    LEFT JOIN core.drivers d ON d.id = en.driver_id
    LEFT JOIN core.results r
           ON r.session_id = p.session_id AND r.driver_id = en.driver_id
    WHERE p.session_id = :s AND p.engine_version = :v
    ORDER BY p.rank
"""

CONTEXT_QUERY = """
    SELECT
      (SELECT count(*) FROM core.results
        WHERE session_id = :s
          AND status IN ('Retired', 'Withdrew', 'Disqualified')) AS n_retirements,
      (SELECT max(degradation_s_per_lap) FROM mart.degradation_curves
        WHERE session_id = :s AND engine_version = :v
          AND degradation_s_per_lap > 0) AS worst_degradation,
      (SELECT compound FROM mart.degradation_curves
        WHERE session_id = :s AND engine_version = :v
          AND degradation_s_per_lap > 0
        ORDER BY degradation_s_per_lap DESC LIMIT 1) AS worst_compound,
      (SELECT round(avg(n))::int FROM (
          SELECT count(*) AS n FROM core.pit_stops
           WHERE session_id = :s GROUP BY driver_number
       ) per_driver) AS typical_stops
"""


class _Row:
    """Adapts a database row to the attribute access the insight rules expect."""

    def __init__(self, mapping: dict[str, Any]) -> None:
        self.driver_number = str(mapping["driver_number"])
        self.abbreviation = mapping.get("abbreviation")
        self.rank = int(mapping["rank"])
        self.gap_to_best_s = float(mapping["gap_to_best_s"] or 0.0)
        self.std_s = float(mapping.get("std_s") or 0.0)
        finish = mapping.get("finish_position")
        self.finish_position = float(finish) if finish is not None else None
        self.did_not_finish = bool(mapping.get("did_not_finish"))


def _entry(mapping: dict[str, Any]) -> PodiumEntryOut:
    return PodiumEntryOut(
        position=int(mapping["position"]),
        driver_number=str(mapping["driver_number"]),
        abbreviation=mapping.get("abbreviation"),
        team=mapping.get("team"),
        status=mapping.get("status"),
        pace_rank=(
            int(mapping["pace_rank"]) if mapping.get("pace_rank") is not None else None
        ),
        gap_to_best_s=(
            float(mapping["gap_to_best_s"])
            if mapping.get("gap_to_best_s") is not None
            else None
        ),
    )


@router.get("/insights/{session_id}", response_model=RaceInsightsResponse)
def get_insights(session_id: int) -> RaceInsightsResponse:
    """Who won, who was quickest, and what was notable about the race."""
    cache = get_cache()
    key = ResponseCache.key("insights", {"session": session_id})
    if (hit := cache.get(key)) is not None:
        return RaceInsightsResponse(**hit)

    params = {"s": session_id, "v": ENGINE_VERSION}
    with get_engine().connect() as conn:
        results = [dict(r) for r in conn.execute(text(RESULTS_QUERY), params).mappings()]
        pace_rows = [dict(r) for r in conn.execute(text(PACE_QUERY), params).mappings()]
        context = dict(conn.execute(text(CONTEXT_QUERY), params).mappings().one())

    if not results and not pace_rows:
        raise HTTPException(
            status_code=404,
            detail=f"no analysis for session {session_id}. Run `f1x analyse` first.",
        )

    winner = _entry(results[0]) if results else None
    podium = [_entry(row) for row in results[:3]]

    pace = [_Row(row) for row in pace_rows]
    fastest = None
    if pace_rows:
        best = pace_rows[0]
        fastest = PodiumEntryOut(
            # The pace leader's "position" is their pace rank: this is the ranking the
            # engine produced, not a finishing order, and conflating the two is the
            # misreading the whole endpoint exists to prevent.
            position=int(best["rank"]),
            driver_number=str(best["driver_number"]),
            abbreviation=best.get("abbreviation"),
            status=None,
            pace_rank=int(best["rank"]),
            gap_to_best_s=0.0,
        )

    found = build_insights(
        pace,
        winner_number=winner.driver_number if winner else None,
        n_retirements=int(context.get("n_retirements") or 0),
        degradation_s_per_lap=(
            float(context["worst_degradation"])
            if context.get("worst_degradation") is not None
            else None
        ),
        worst_compound=context.get("worst_compound"),
        # Pit loss is computed on demand by the strategy engine rather than stored,
        # so the stop count travels without it here and the strategy rule stays quiet.
        optimal_stops=(
            int(context["typical_stops"])
            if context.get("typical_stops") is not None
            else None
        ),
    )

    response = RaceInsightsResponse(
        session_id=session_id,
        meta=Meta(engine_version=ENGINE_VERSION),
        winner=winner,
        fastest_on_pace=fastest,
        podium=podium,
        insights=[
            InsightOut(
                kind=item.kind,
                headline=item.headline,
                detail=item.detail,
                magnitude=item.magnitude,
            )
            for item in found
        ],
    )
    cache.set(key, response.model_dump())
    return response
