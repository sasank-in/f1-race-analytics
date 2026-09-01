"""Database boundary for telemetry analysis.

Telemetry is the one place where loading everything would be a mistake: a session holds
around 700,000 car-data rows. Every query here is scoped to a single driver-lap and
bounded by the lap's own time window, so the hypertable is read through its index
rather than scanned.
"""

from __future__ import annotations

import polars as pl
from sqlalchemy import Engine, text

from f1x.engine.telemetry.alignment import AlignedLap, align_lap
from f1x.engine.telemetry.corners import Corner, compare_corners, detect_corners
from f1x.engine.telemetry.delta import DeltaTrace, compute_delta

# The lap window comes from core.laps, so telemetry is filtered on session time rather
# than pulled per driver and sliced in Python.
LAP_WINDOW_QUERY = """
    SELECT lap_start_s, lap_end_s
    FROM core.laps
    WHERE session_id = :session_id
      AND driver_number = :driver_number
      AND lap_number = :lap_number
"""

TELEMETRY_QUERY = """
    SELECT session_s, speed, throttle, brake, gear
    FROM core.telemetry
    WHERE session_id = :session_id
      AND driver_number = :driver_number
      AND session_s BETWEEN :start_s AND :end_s
    ORDER BY session_s
"""


def load_lap_telemetry(
    engine: Engine, session_id: int, driver_number: str, lap_number: int
) -> AlignedLap | None:
    """Load one driver-lap's telemetry and resample it onto a distance grid."""
    with engine.connect() as conn:
        window = conn.execute(
            text(LAP_WINDOW_QUERY),
            {
                "session_id": session_id,
                "driver_number": driver_number,
                "lap_number": lap_number,
            },
        ).one_or_none()
        if window is None or window.lap_start_s is None or window.lap_end_s is None:
            return None

        rows = conn.execute(
            text(TELEMETRY_QUERY),
            {
                "session_id": session_id,
                "driver_number": driver_number,
                "start_s": window.lap_start_s,
                "end_s": window.lap_end_s,
            },
        )
        records = [dict(row) for row in rows.mappings()]

    if not records:
        return None
    return align_lap(
        pl.DataFrame(records), driver_number=driver_number, lap_number=lap_number
    )


def compare_laps(
    engine: Engine,
    session_id: int,
    reference: tuple[str, int],
    comparison: tuple[str, int],
) -> tuple[DeltaTrace, list[tuple[Corner, Corner, float]]] | None:
    """Compare two driver-laps: cumulative delta and matched corner speeds."""
    reference_lap = load_lap_telemetry(engine, session_id, *reference)
    comparison_lap = load_lap_telemetry(engine, session_id, *comparison)
    if reference_lap is None or comparison_lap is None:
        return None

    trace = compute_delta(reference_lap, comparison_lap)
    if trace is None:
        return None

    matches = compare_corners(
        detect_corners(reference_lap), detect_corners(comparison_lap)
    )
    return trace, matches
