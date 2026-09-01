"""Database boundary for the transform layer.

The transform functions themselves are pure. This module is the only part of the
package that reads laps out of ``core`` and writes metrics into ``mart``, which keeps
the analysis code testable on frames alone.
"""

from __future__ import annotations

import polars as pl
from sqlalchemy import Engine, text

from f1x.config import ENGINE_VERSION
from f1x.transform.pipeline import TransformResult, transform_session

LAP_QUERY = """
    SELECT session_id, driver_number, lap_number, lap_time_s,
           sector1_s, sector2_s, sector3_s,
           stint, compound::text AS compound, tyre_life, fresh_tyre,
           pit_in_s, pit_out_s, position, track_status,
           deleted, is_accurate
    FROM core.laps
    WHERE session_id = :session_id
    ORDER BY driver_number, lap_number
"""


def load_laps(engine: Engine, session_id: int) -> pl.DataFrame:
    """Read one session's laps into a Polars frame."""
    with engine.connect() as conn:
        rows = conn.execute(text(LAP_QUERY), {"session_id": session_id})
        records = [dict(row) for row in rows.mappings()]
    if not records:
        return pl.DataFrame()
    return pl.DataFrame(records)


def session_total_laps(engine: Engine, session_id: int) -> int | None:
    """Scheduled distance, needed for fuel correction. Null outside races."""
    with engine.connect() as conn:
        return conn.execute(
            text("SELECT total_laps FROM core.sessions WHERE id = :s"),
            {"s": session_id},
        ).scalar_one_or_none()


def session_fuel_effect(engine: Engine, session_id: int) -> float | None:
    """The circuit's fitted fuel coefficient, or None to use the default."""
    with engine.connect() as conn:
        return conn.execute(
            text(
                "SELECT c.fuel_effect_s_per_kg "
                "FROM core.sessions s "
                "JOIN core.events e ON e.id = s.event_id "
                "JOIN core.circuits c ON c.id = e.circuit_id "
                "WHERE s.id = :s"
            ),
            {"s": session_id},
        ).scalar_one_or_none()


def transform_and_store(engine: Engine, session_id: int) -> TransformResult:
    """Transform one session and replace its rows in mart.lap_metrics.

    Rows are replaced for this ``(session_id, engine_version)`` pair only, so
    recomputing under a new engine version leaves the previous results intact for
    comparison rather than silently overwriting them.
    """
    laps = load_laps(engine, session_id)
    result = transform_session(
        laps,
        total_laps=session_total_laps(engine, session_id),
        fuel_effect=session_fuel_effect(engine, session_id),
    )

    with engine.begin() as conn:
        conn.execute(
            text(
                "DELETE FROM mart.lap_metrics "
                "WHERE session_id = :s AND engine_version = :v"
            ),
            {"s": session_id, "v": ENGINE_VERSION},
        )
        if not result.lap_metrics.is_empty():
            rows = result.lap_metrics.with_columns(
                engine_version=pl.lit(ENGINE_VERSION)
            ).to_dicts()
            conn.execute(
                text(
                    "INSERT INTO mart.lap_metrics ("
                    "session_id, driver_number, lap_number, lap_time_s, "
                    "is_green, is_in_lap, is_out_lap, is_representative, exclusion_reason, "
                    "fuel_corrected_s, fuel_load_kg, evolution_corrected_s, "
                    "gap_ahead_s, gap_behind_s, is_clean_air, "
                    "stint, compound, tyre_life, engine_version) "
                    "VALUES (:session_id, :driver_number, :lap_number, :lap_time_s, "
                    ":is_green, :is_in_lap, :is_out_lap, :is_representative, :exclusion_reason, "
                    ":fuel_corrected_s, :fuel_load_kg, :evolution_corrected_s, "
                    ":gap_ahead_s, :gap_behind_s, :is_clean_air, "
                    ":stint, :compound, :tyre_life, :engine_version)"
                ),
                rows,
            )

        _replace_derived(conn, session_id, result)

    return result


def _replace_derived(conn: object, session_id: int, result: TransformResult) -> None:
    """Rewrite the stint and pit-stop projections for this session."""
    from sqlalchemy import Connection

    assert isinstance(conn, Connection)  # noqa: S101 - internal invariant

    conn.execute(text("DELETE FROM core.stints WHERE session_id = :s"), {"s": session_id})
    if not result.stints.is_empty():
        conn.execute(
            text(
                "INSERT INTO core.stints "
                "(session_id, driver_number, stint, compound, start_lap, end_lap, "
                " n_laps, tyre_age_start, fresh_tyre) "
                "VALUES (:session_id, :driver_number, :stint, CAST(:compound AS core.compound), "
                ":start_lap, :end_lap, :n_laps, :tyre_age_start, :fresh_tyre)"
            ),
            result.stints.to_dicts(),
        )

    conn.execute(text("DELETE FROM core.pit_stops WHERE session_id = :s"), {"s": session_id})
    if not result.pit_stops.is_empty():
        conn.execute(
            text(
                "INSERT INTO core.pit_stops "
                "(session_id, driver_number, stop_number, lap_number, pit_in_s, pit_out_s, "
                " pit_duration_s, compound_in, compound_out) "
                "VALUES (:session_id, :driver_number, :stop_number, :lap_number, :pit_in_s, "
                ":pit_out_s, :pit_duration_s, CAST(:compound_in AS core.compound), "
                "CAST(:compound_out AS core.compound))"
            ),
            result.pit_stops.to_dicts(),
        )
