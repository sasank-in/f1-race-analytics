"""Telemetry comparison endpoint.

The only endpoint that touches the hypertables directly. Every query is scoped to a
single driver-lap and bounded by that lap's time window, so a request reads through
the index rather than scanning 16 million rows.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from f1x.api.deps import ResponseCache, get_cache, get_engine
from f1x.api.schemas import CornerDeltaOut, Meta, TelemetryCompareResponse
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
