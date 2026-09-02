"""Telemetry comparison endpoint.

The only endpoint that touches the hypertables directly. Every query is scoped to a
single driver-lap and bounded by that lap's time window, so a request reads through
the index rather than scanning 16 million rows.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from f1x.api.deps import ResponseCache, get_cache, get_engine
from f1x.api.schemas import (
    CornerDeltaOut,
    CornerOut,
    Meta,
    TelemetryCompareResponse,
    TrackMapResponse,
    TrackPointOut,
)
from f1x.config import ENGINE_VERSION

router = APIRouter(prefix="/telemetry", tags=["telemetry"])

# One point per metre is more resolution than any chart needs. Downsampling keeps a
# response in the tens of kilobytes rather than several hundred.
TRACE_POINTS = 500


@router.get("/compare/{session_id}", response_model=TelemetryCompareResponse)
def compare_laps(
    session_id: int,
    driver_a: str,
    lap_a: int,
    driver_b: str,
    lap_b: int,
) -> TelemetryCompareResponse:
    """Compare two laps: cumulative delta time and corner-by-corner minimum speeds.

    The delta trace answers *where* a lap was won, not just by how much. A step down
    under braking is a later brake point; a rise on the straight after a corner means
    the time was actually won in the corner before it.
    """
    cache = get_cache()
    key = ResponseCache.key(
        "telemetry_compare",
        {
            "session_id": session_id,
            "a": f"{driver_a}:{lap_a}",
            "b": f"{driver_b}:{lap_b}",
        },
    )
    if (hit := cache.get(key)) is not None:
        return TelemetryCompareResponse(**hit)

    from f1x.engine.telemetry.repository import compare_laps as run_comparison

    result = run_comparison(
        get_engine(), session_id, (driver_a, lap_a), (driver_b, lap_b)
    )
    if result is None:
        raise HTTPException(
            status_code=404,
            detail=(
                f"no telemetry for session {session_id} laps "
                f"{driver_a}/{lap_a} and {driver_b}/{lap_b}. "
                "Ingest the session with telemetry first."
            ),
        )

    trace, matches = result
    # Downsample evenly; the shape is what matters, not every metre.
    step = max(1, trace.distance_m.size // TRACE_POINTS)
    response = TelemetryCompareResponse(
        session_id=session_id,
        meta=Meta(engine_version=ENGINE_VERSION),
        reference_driver=trace.reference_driver,
        comparison_driver=trace.comparison_driver,
        final_delta_s=trace.final_delta_s,
        distance_m=[float(x) for x in trace.distance_m[::step]],
        delta_s=[float(x) for x in trace.delta_s[::step]],
        corners=[
            CornerDeltaOut(
                index=ref.index,
                apex_distance_m=ref.apex_distance_m,
                reference_min_speed_kmh=ref.min_speed_kmh,
                comparison_min_speed_kmh=ref.min_speed_kmh + delta,
                delta_kmh=delta,
            )
            for ref, _, delta in matches
        ],
    )
    cache.set(key, response.model_dump())
    return response


@router.get("/map/{session_id}", response_model=TrackMapResponse)
def track_map(session_id: int, driver: str, lap: int) -> TrackMapResponse:
    """The circuit drawn from one lap's positional trace, coloured by speed.

    Nothing about the circuit is stored: the shape comes from where the car actually
    went, so any layout the pipeline has data for can be drawn without a map to
    maintain. Corner apexes are placed by translating the detected distance back onto
    the geometry.
    """
    cache = get_cache()
    key = ResponseCache.key(
        "track_map", {"session_id": session_id, "driver": driver, "lap": lap}
    )
    if (hit := cache.get(key)) is not None:
        return TrackMapResponse(**hit)

    from sqlalchemy import text

    from f1x.engine.telemetry.corners import detect_corners
    from f1x.engine.telemetry.repository import load_lap_telemetry, load_track_map
    from f1x.engine.telemetry.track_map import downsample

    engine = get_engine()
    track = load_track_map(engine, session_id, driver, lap)
    if track is None:
        raise HTTPException(
            status_code=404,
            detail=(
                f"no positional data for session {session_id} car {driver} lap {lap}. "
                "Ingest the session with telemetry first."
            ),
        )

    thinned = downsample(track)
    aligned = load_lap_telemetry(engine, session_id, driver, lap)
    corners = detect_corners(aligned) if aligned is not None else []

    with engine.connect() as conn:
        lap_time = conn.execute(
            text(
                "SELECT lap_time_s FROM core.laps "
                "WHERE session_id = :s AND driver_number = :d AND lap_number = :l"
            ),
            {"s": session_id, "d": driver, "l": lap},
        ).scalar_one_or_none()

    response = TrackMapResponse(
        session_id=session_id,
        meta=Meta(engine_version=ENGINE_VERSION),
        driver_number=driver,
        lap_number=lap,
        lap_time_s=float(lap_time) if lap_time is not None else None,
        lap_distance_m=track.lap_distance_m,
        min_speed_kmh=float(thinned.speed_kmh.min()),
        max_speed_kmh=float(thinned.speed_kmh.max()),
        points=[
            TrackPointOut(
                x=float(x), y=float(y), speed_kmh=float(v), distance_m=float(d)
            )
            for x, y, v, d in zip(
                thinned.x, thinned.y, thinned.speed_kmh, thinned.distance_m, strict=True
            )
        ],
        corners=[
            CornerOut(
                index=c.index,
                apex_distance_m=c.apex_distance_m,
                min_speed_kmh=c.min_speed_kmh,
                entry_speed_kmh=c.entry_speed_kmh,
                exit_speed_kmh=c.exit_speed_kmh,
                braking_point_m=c.braking_point_m,
                throttle_point_m=c.throttle_point_m,
            )
            for c in corners
        ],
    )
    cache.set(key, response.model_dump())
    return response
