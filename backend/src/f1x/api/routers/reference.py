"""Reference endpoints: seasons, events and sessions.

These are the navigation layer. Everything else is addressed by a session id, so this
is how a client discovers what ids exist.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import text

from f1x.api.deps import get_engine
from f1x.api.schemas import EventOut, SeasonOut, SessionOut

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
