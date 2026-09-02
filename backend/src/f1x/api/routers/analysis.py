"""Analysis endpoints: laps, pace, degradation.

Every response carries the engine version that produced it. Two clients holding
different versions of a number can then tell why they differ, instead of arguing
about which is right.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import text

from f1x.api.deps import get_engine
from f1x.api.schemas import (
    DegradationOut,
    DegradationResponse,
    LapOut,
    Meta,
    PaceOut,
    PaceResponse,
    StintFitOut,
)
from f1x.config import ENGINE_VERSION

router = APIRouter(prefix="/analysis", tags=["analysis"])


def _meta(session_id: int) -> Meta:
    """Provenance for a session's derived values."""
    with get_engine().connect() as conn:
        computed = conn.execute(
            text(
                "SELECT max(computed_at) FROM mart.lap_metrics "
                "WHERE session_id = :s AND engine_version = :v"
            ),
            {"s": session_id, "v": ENGINE_VERSION},
        ).scalar_one_or_none()
    return Meta(engine_version=ENGINE_VERSION, computed_at=computed)


def _require_analysed(session_id: int, table: str, what: str) -> None:
    """404 with an actionable message when a session has not been through the engine."""
    with get_engine().connect() as conn:
        # Table names come from this module's own call sites, never from user input.
        found = conn.execute(
            text(
                f"SELECT 1 FROM {table} WHERE session_id = :s AND engine_version = :v "  # noqa: S608
                "LIMIT 1"
            ),
            {"s": session_id, "v": ENGINE_VERSION},
        ).scalar_one_or_none()
    if found is None:
        raise HTTPException(
            status_code=404,
            detail=(
                f"no {what} for session {session_id} at engine version "
                f"{ENGINE_VERSION}. Run `f1x transform` and `f1x analyse` first."
            ),
        )


@router.get("/laps/{session_id}", response_model=list[LapOut])
def get_laps(
    session_id: int,
    driver: str | None = Query(default=None, description="Restrict to one car number"),
    representative_only: bool = Query(
        default=False, description="Only laps that belong in a pace sample"
    ),
) -> list[LapOut]:
    """Per-lap metrics, including why any lap was excluded."""
    _require_analysed(session_id, "mart.lap_metrics", "lap metrics")

    sql = (
        "SELECT driver_number, lap_number, lap_time_s, compound, tyre_life, stint, "
        "       is_representative, exclusion_reason, fuel_corrected_s, position "
        "FROM mart.lap_metrics WHERE session_id = :s AND engine_version = :v "
    )
    params: dict[str, object] = {"s": session_id, "v": ENGINE_VERSION}
    if driver:
        sql += "AND driver_number = :d "
        params["d"] = driver
    if representative_only:
        sql += "AND is_representative "
    sql += "ORDER BY driver_number, lap_number"

    with get_engine().connect() as conn:
        return [LapOut(**dict(row)) for row in conn.execute(text(sql), params).mappings()]


@router.get("/pace/{session_id}", response_model=PaceResponse)
def get_pace(session_id: int) -> PaceResponse:
    """Driver pace, ranked.

    Pace is the 20th percentile of clean fuel-corrected laps — not the fastest lap,
    which rewards whoever had the best single opportunity, and not the mean, which
    rewards whoever avoided traffic.
    """
    _require_analysed(session_id, "mart.pace_rankings", "pace rankings")

    with get_engine().connect() as conn:
        rows = conn.execute(
            text(
                "SELECT driver_number, rank, n_laps, pace_s, gap_to_best_s, best_s, "
                "       median_s, std_s, clean_air_laps "
                "FROM mart.pace_rankings "
                "WHERE session_id = :s AND engine_version = :v ORDER BY rank"
            ),
            {"s": session_id, "v": ENGINE_VERSION},
        ).mappings()
        drivers = [PaceOut(**dict(row)) for row in rows]

    return PaceResponse(session_id=session_id, meta=_meta(session_id), drivers=drivers)


@router.get("/degradation/{session_id}", response_model=DegradationResponse)
def get_degradation(session_id: int) -> DegradationResponse:
    """Tyre degradation by compound, with the stint fits behind it.

    A stint fit may report a negative slope when the run was too short to support an
    estimate. Those are returned with ``is_physical`` false rather than hidden — the
    failure is information about the stint.
    """
    _require_analysed(session_id, "mart.stint_fits", "stint fits")

    with get_engine().connect() as conn:
        compounds = [
            DegradationOut(**dict(row))
            for row in conn.execute(
                text(
                    "SELECT compound, n_stints, n_laps, degradation_s_per_lap, "
                    "       degradation_iqr_s, median_pace_s, max_stint_laps "
                    "FROM mart.degradation_curves "
                    "WHERE session_id = :s AND engine_version = :v "
                    "ORDER BY degradation_s_per_lap DESC"
                ),
                {"s": session_id, "v": ENGINE_VERSION},
            ).mappings()
        ]
        stints = [
            StintFitOut(**dict(row))
            for row in conn.execute(
                text(
                    "SELECT driver_number, stint, compound, n_laps, pace_s, "
                    "       degradation_s_per_lap, is_physical, is_reliable, r_squared, "
                    "       tyre_age_range, excluded_lap_count "
                    "FROM mart.stint_fits "
                    "WHERE session_id = :s AND engine_version = :v "
                    "ORDER BY driver_number, stint"
                ),
                {"s": session_id, "v": ENGINE_VERSION},
            ).mappings()
        ]

    return DegradationResponse(
        session_id=session_id,
        meta=_meta(session_id),
        compounds=compounds,
        stints=stints,
    )
