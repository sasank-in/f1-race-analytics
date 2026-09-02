"""Reference endpoints: seasons, events and sessions.

These are the navigation layer. Everything else is addressed by a session id, so this
is how a client discovers what ids exist.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import text

from f1x.api.deps import ResponseCache, get_cache, get_engine
from f1x.api.schemas import EventOut, SeasonOut, SessionOut, SessionSummaryOut
from f1x.config import ENGINE_VERSION

router = APIRouter(tags=["reference"])


@router.get("/seasons", response_model=list[SeasonOut])
def list_seasons() -> list[SeasonOut]:
    """Every ingested season."""
    with get_engine().connect() as conn:
        rows = conn.execute(
            text(
                "SELECT s.year, s.has_telemetry, count(e.id) AS n_events "
                "FROM core.seasons s LEFT JOIN core.events e ON e.season_year = s.year "
                "GROUP BY 1, 2 ORDER BY 1 DESC"
            )
        ).mappings()
        return [SeasonOut(**dict(row)) for row in rows]


@router.get("/events", response_model=list[EventOut])
def list_events(
    season: int | None = Query(default=None, description="Restrict to one season"),
) -> list[EventOut]:
    """Grand prix weekends, newest first."""
    sql = (
        "SELECT e.id, e.season_year, e.round, e.name, e.country, e.event_date, "
        "       c.key AS circuit_key "
        "FROM core.events e LEFT JOIN core.circuits c ON c.id = e.circuit_id "
    )
    params: dict[str, object] = {}
    if season is not None:
        sql += "WHERE e.season_year = :season "
        params["season"] = season
    sql += "ORDER BY e.season_year DESC, e.round"

    with get_engine().connect() as conn:
        rows = conn.execute(text(sql), params).mappings()
        return [EventOut(**dict(row)) for row in rows]


@router.get("/sessions", response_model=list[SessionOut])
def list_sessions(
    season: int | None = Query(default=None),
    kind: str | None = Query(default=None, description="FP1-3, Q, SQ, S or R"),
) -> list[SessionOut]:
    """Timed sessions. Every analysis endpoint is keyed by one of these ids."""
    sql = (
        "SELECT s.id, s.event_id, s.kind::text AS kind, s.total_laps, "
        "       s.telemetry_loaded, s.start_utc, "
        "       e.name AS event_name, e.season_year, e.round "
        "FROM core.sessions s JOIN core.events e ON e.id = s.event_id WHERE 1 = 1 "
    )
    params: dict[str, object] = {}
    if season is not None:
        sql += "AND e.season_year = :season "
        params["season"] = season
    if kind is not None:
        sql += "AND s.kind::text = :kind "
        params["kind"] = kind.upper()
    sql += "ORDER BY e.season_year DESC, e.round, s.kind"

    with get_engine().connect() as conn:
        rows = conn.execute(text(sql), params).mappings()
        return [SessionOut(**dict(row)) for row in rows]


@router.get("/sessions/{session_id}", response_model=SessionOut)
def get_session(session_id: int) -> SessionOut:
    """One session."""
    with get_engine().connect() as conn:
        row = conn.execute(
            text(
                "SELECT s.id, s.event_id, s.kind::text AS kind, s.total_laps, "
                "       s.telemetry_loaded, s.start_utc, "
                "       e.name AS event_name, e.season_year, e.round "
                "FROM core.sessions s JOIN core.events e ON e.id = s.event_id "
                "WHERE s.id = :s"
            ),
            {"s": session_id},
        ).mappings().one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail=f"session {session_id} not found")
    return SessionOut(**dict(row))


# One row per analysed race: winner, quickest car, retirements, optimal stop count.
# Built in SQL rather than by fanning out per-session requests, which at 44 races
# would be 44 round trips to render one list.
SUMMARY_QUERY = """
    WITH winner AS (
        SELECT r.session_id, en.driver_number
        FROM core.results r
        JOIN core.entries en
          ON en.session_id = r.session_id AND en.driver_id = r.driver_id
        WHERE r.position = 1
    ),
    quickest AS (
        SELECT DISTINCT ON (session_id) session_id, driver_number
        FROM mart.pace_rankings
        WHERE engine_version = :v
        ORDER BY session_id, rank
    ),
    retirements AS (
        SELECT session_id, count(*) AS n
        FROM core.results
        WHERE status IN ('Retired', 'Withdrew', 'Disqualified')
        GROUP BY session_id
    ),
    stops AS (
        SELECT session_id,
               round(avg(n_stops)) AS typical_stops
        FROM (
            SELECT session_id, driver_number, count(*) AS n_stops
            FROM core.pit_stops GROUP BY 1, 2
        ) per_driver
        GROUP BY session_id
    )
    SELECT s.id AS session_id, e.name AS event_name, e.season_year, e.round,
           s.total_laps, s.telemetry_loaded,
           w.driver_number AS winner,
           q.driver_number AS fastest_driver,
           coalesce(rt.n, 0) AS n_retirements,
           st.typical_stops AS optimal_stops
    FROM core.sessions s
    JOIN core.events e ON e.id = s.event_id
    LEFT JOIN winner w ON w.session_id = s.id
    LEFT JOIN quickest q ON q.session_id = s.id
    LEFT JOIN retirements rt ON rt.session_id = s.id
    LEFT JOIN stops st ON st.session_id = s.id
    -- Cast required: with no season passed, Postgres cannot infer the type of a
    -- bare NULL parameter and rejects the statement outright.
    WHERE (cast(:season AS int) IS NULL OR e.season_year = cast(:season AS int))
    ORDER BY e.season_year DESC, e.round
"""


def _headline(row: Mapping[str, Any]) -> str:
    """One sentence per race.

    Deliberately states the *tension* where there is one. "Verstappen won" is a
    result; "Leclerc was quickest and did not win" is a reason to open the race.
    """
    winner: str | None = row.get("winner")
    fastest: str | None = row.get("fastest_driver")
    retirements = int(row.get("n_retirements") or 0)
    stops: float | None = row.get("optimal_stops")

    parts: list[str] = []
    if winner and fastest and winner != fastest:
        # The interesting case, stated first: pace and result disagreed.
        parts.append(f"Car {fastest} had the pace; car {winner} won")
    elif winner:
        parts.append(f"Car {winner} won, and was quickest")
    elif fastest:
        parts.append(f"Car {fastest} was quickest")

    if stops:
        parts.append(f"{int(stops)}-stop race")
    if retirements:
        parts.append(f"{retirements} retirement{'s' if retirements != 1 else ''}")

    return ". ".join(parts) + "." if parts else "Not yet analysed."


@router.get("/summaries", response_model=list[SessionSummaryOut])
def list_summaries(
    season: int | None = Query(default=None, description="Restrict to one season"),
) -> list[SessionSummaryOut]:
    """Each race in one line, so a list of 44 becomes navigable.

    The mismatch flag is the useful part: a race where the quickest car did not win
    is where the interesting analysis lives.
    """
    cache = get_cache()
    key = ResponseCache.key("summaries", {"season": season})
    if (hit := cache.get(key)) is not None:
        return [SessionSummaryOut(**row) for row in hit]

    with get_engine().connect() as conn:
        rows = [
            dict(r)
            for r in conn.execute(
                text(SUMMARY_QUERY), {"season": season, "v": ENGINE_VERSION}
            ).mappings()
        ]

    summaries = [
        SessionSummaryOut(
            session_id=int(row["session_id"]),
            event_name=str(row["event_name"]),
            season_year=int(row["season_year"]),
            round=int(row["round"]),
            total_laps=row["total_laps"],
            telemetry_loaded=bool(row["telemetry_loaded"]),
            winner=row["winner"],
            fastest_driver=row["fastest_driver"],
            pace_winner_mismatch=bool(
                row["winner"] and row["fastest_driver"] and row["winner"] != row["fastest_driver"]
            ),
            n_retirements=int(row["n_retirements"] or 0),
            optimal_stops=int(row["optimal_stops"]) if row["optimal_stops"] else None,
            headline=_headline(row),
        )
        for row in rows
    ]
    cache.set(key, [s.model_dump() for s in summaries])
    return summaries
