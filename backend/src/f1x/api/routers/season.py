"""Season-level views.

Every other endpoint answers a question about one race. These answer questions that
only exist across a calendar: which circuits punish tyres, and how a driver's pace
moved through a season.

Both aggregate in SQL rather than fanning out per-race requests. At 44 races the
alternative is 44 round trips to render one page.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import text

from f1x.api.deps import ResponseCache, get_cache, get_engine
from f1x.api.schemas import (
    CircuitProfileOut,
    Meta,
    SeasonPaceResponse,
    SeasonPaceRowOut,
    SeasonProfileResponse,
)
from f1x.config import ENGINE_VERSION

router = APIRouter(tags=["season"])

# Circuit character, pooled over every race held there.
#
# Only physical degradation curves count: a negative fit means the stint was too short
# to support an estimate, and averaging those in would drag a circuit's figure toward
# zero for a reason that has nothing to do with its tarmac.
CIRCUIT_PROFILE_QUERY = """
    WITH deg AS (
        SELECT c.key AS circuit_key,
               max(c.name) AS circuit_name,
               count(DISTINCT s.id) AS n_races,
               array_agg(DISTINCT e.season_year ORDER BY e.season_year) AS seasons,
               percentile_cont(0.5) WITHIN GROUP (ORDER BY dc.degradation_s_per_lap)
                 AS degradation,
               avg(dc.max_stint_laps) AS stint_laps
        FROM mart.degradation_curves dc
        JOIN core.sessions s ON s.id = dc.session_id
        JOIN core.events e ON e.id = s.event_id
        JOIN core.circuits c ON c.id = e.circuit_id
        WHERE dc.engine_version = :v
          AND dc.degradation_s_per_lap > 0
          AND (cast(:season AS int) IS NULL OR e.season_year = cast(:season AS int))
        GROUP BY c.key
    ),
    pace AS (
        SELECT c.key AS circuit_key,
               percentile_cont(0.5) WITHIN GROUP (ORDER BY m.lap_time_s) AS reference_lap
        FROM mart.lap_metrics m
        JOIN core.sessions s ON s.id = m.session_id
        JOIN core.events e ON e.id = s.event_id
        JOIN core.circuits c ON c.id = e.circuit_id
        WHERE m.engine_version = :v AND m.is_representative
          AND (cast(:season AS int) IS NULL OR e.season_year = cast(:season AS int))
        GROUP BY c.key
    ),
    stops AS (
        SELECT c.key AS circuit_key, avg(per_driver.n) AS typical_stops
        FROM (
            SELECT session_id, driver_number, count(*) AS n
            FROM core.pit_stops GROUP BY 1, 2
        ) per_driver
        JOIN core.sessions s ON s.id = per_driver.session_id
        JOIN core.events e ON e.id = s.event_id
        JOIN core.circuits c ON c.id = e.circuit_id
        WHERE (cast(:season AS int) IS NULL OR e.season_year = cast(:season AS int))
        GROUP BY c.key
    )
    SELECT deg.*, pace.reference_lap, stops.typical_stops
    FROM deg
    LEFT JOIN pace USING (circuit_key)
    LEFT JOIN stops USING (circuit_key)
    ORDER BY deg.degradation DESC
"""


@router.get("/season/circuits", response_model=SeasonProfileResponse)
def circuit_profiles(
    season: int | None = Query(default=None, description="Restrict to one season"),
) -> SeasonProfileResponse:
    """Which circuits punish tyres, ranked.

    A strategist's first question about an unfamiliar track, and one that only has an
    answer across a calendar: a single race tells you what happened there, not how the
    circuit compares.
    """
    cache = get_cache()
    key = ResponseCache.key("season_circuits", {"season": season})
    if (hit := cache.get(key)) is not None:
        return SeasonProfileResponse(**hit)

    with get_engine().connect() as conn:
        rows = [
            dict(r)
            for r in conn.execute(
                text(CIRCUIT_PROFILE_QUERY), {"season": season, "v": ENGINE_VERSION}
            ).mappings()
        ]

    if not rows:
        raise HTTPException(
            status_code=404,
            detail="no degradation curves yet. Run `f1x analyse all` first.",
        )

    response = SeasonProfileResponse(
        meta=Meta(engine_version=ENGINE_VERSION),
        season=season,
        circuits=[
            CircuitProfileOut(
                circuit_key=row["circuit_key"],
                circuit_name=row["circuit_name"],
                n_races=int(row["n_races"]),
                seasons=list(row["seasons"]),
                degradation_s_per_lap=float(row["degradation"]),
                typical_stint_laps=float(row["stint_laps"]),
                typical_stops=float(row["typical_stops"]) if row["typical_stops"] else None,
                reference_lap_s=float(row["reference_lap"]) if row["reference_lap"] else None,
            )
            for row in rows
        ],
    )
    cache.set(key, response.model_dump())
    return response


SEASON_PACE_QUERY = """
    SELECT e.round, p.driver_number, p.gap_to_best_s, p.rank
    FROM mart.pace_rankings p
    JOIN core.sessions s ON s.id = p.session_id
    JOIN core.events e ON e.id = s.event_id
    WHERE e.season_year = :season AND p.engine_version = :v
    ORDER BY e.round, p.rank
"""


@router.get("/season/pace/{season}", response_model=SeasonPaceResponse)
def season_pace(season: int) -> SeasonPaceResponse:
    """Every driver's pace gap, race by race.

    The shape of a season: who improved, who fell away, and where a car's upgrade
    actually landed. A per-race ranking cannot show any of that.
    """
    cache = get_cache()
    key = ResponseCache.key("season_pace", {"season": season})
    if (hit := cache.get(key)) is not None:
        return SeasonPaceResponse(**hit)

    with get_engine().connect() as conn:
        rows = [
            dict(r)
            for r in conn.execute(
                text(SEASON_PACE_QUERY), {"season": season, "v": ENGINE_VERSION}
            ).mappings()
        ]

    if not rows:
        raise HTTPException(
            status_code=404,
            detail=f"no pace rankings for {season}. Run `f1x analyse all` first.",
        )

    rounds = sorted({int(r["round"]) for r in rows})
    by_driver: dict[str, dict[int, float]] = {}
    wins: dict[str, int] = {}
    for row in rows:
        driver = str(row["driver_number"])
        by_driver.setdefault(driver, {})[int(row["round"])] = float(row["gap_to_best_s"])
        if int(row["rank"]) == 1:
            wins[driver] = wins.get(driver, 0) + 1

    drivers = []
    for driver, gaps_by_round in by_driver.items():
        values = list(gaps_by_round.values())
        drivers.append(
            SeasonPaceRowOut(
                driver_number=driver,
                n_races=len(values),
                mean_gap_s=sum(values) / len(values),
                best_gap_s=min(values),
                worst_gap_s=max(values),
                wins_on_pace=wins.get(driver, 0),
                # One slot per round, so a form line has a consistent x-axis even
                # where a driver missed a race.
                gaps=[gaps_by_round.get(r) for r in rounds],
            )
        )

    response = SeasonPaceResponse(
        season=season,
        meta=Meta(engine_version=ENGINE_VERSION),
        rounds=rounds,
        drivers=sorted(drivers, key=lambda d: d.mean_gap_s),
    )
    cache.set(key, response.model_dump())
    return response
