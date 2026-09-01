"""Database boundary for the analysis engine.

Everything under `engine/` is pure. This module is the only part that reads metrics
out of `mart` and writes engine output back, which keeps the models testable on frames.
"""

from __future__ import annotations

from dataclasses import dataclass

import polars as pl
from sqlalchemy import Connection, Engine, text

from f1x.config import ENGINE_VERSION
from f1x.engine.degradation.curves import DegradationCurve, build_curves
from f1x.engine.degradation.curves import to_frame as curves_to_frame
from f1x.engine.pace.ranking import DriverPace, rank_session
from f1x.engine.pace.stint_model import StintFit, fit_session
from f1x.engine.pace.stint_model import to_frame as fits_to_frame


@dataclass(frozen=True)
class AnalysisResult:
    """Everything one session's analysis produces."""

    session_id: int
    stint_fits: list[StintFit]
    ranking: list[DriverPace]
    curves: list[DegradationCurve]
    engine_version: str = ENGINE_VERSION

    @property
    def reliable_fits(self) -> int:
        return sum(1 for fit in self.stint_fits if fit.is_reliable)


def load_metrics(engine: Engine, session_id: int) -> pl.DataFrame:
    """Read one session's lap metrics for the current engine version."""
    with engine.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT * FROM mart.lap_metrics "
                "WHERE session_id = :s AND engine_version = :v "
                "ORDER BY driver_number, lap_number"
            ),
            {"s": session_id, "v": ENGINE_VERSION},
        )
        records = [dict(row) for row in rows.mappings()]
    return pl.DataFrame(records) if records else pl.DataFrame()


def analyse(laps: pl.DataFrame, session_id: int) -> AnalysisResult:
    """Run the pace and degradation models. Pure: frames in, results out."""
    fits = fit_session(laps)
    return AnalysisResult(
        session_id=session_id,
        stint_fits=fits,
        ranking=rank_session(laps),
        curves=build_curves(fits_to_frame(fits)),
    )


def analyse_and_store(engine: Engine, session_id: int) -> AnalysisResult:
    """Analyse one session and replace its engine output.

    Scoped to this ``(session_id, engine_version)`` pair, so recomputing under a new
    version leaves earlier results in place for comparison.
    """
    result = analyse(load_metrics(engine, session_id), session_id)

    with engine.begin() as conn:
        _replace(conn, "mart.stint_fits", session_id, fits_to_frame(result.stint_fits))
        _replace(conn, "mart.pace_rankings", session_id, _ranking_frame(result.ranking))
        _replace(conn, "mart.degradation_curves", session_id, curves_to_frame(result.curves))

    return result


def _ranking_frame(ranking: list[DriverPace]) -> pl.DataFrame:
    from f1x.engine.pace.ranking import to_frame

    return to_frame(ranking)


# Column order per table, so the generated INSERT matches the schema exactly.
_COLUMNS = {
    "mart.stint_fits": (
        "session_id", "driver_number", "stint", "compound", "n_laps", "pace_s",
        "degradation_s_per_lap", "r_squared", "residual_std_s", "tyre_age_start",
        "is_reliable",
    ),
    "mart.pace_rankings": (
        "session_id", "driver_number", "n_laps", "pace_s", "best_s", "median_s",
        "std_s", "clean_air_laps", "gap_to_best_s", "rank",
    ),
    "mart.degradation_curves": (
        "session_id", "compound", "n_stints", "n_laps", "degradation_s_per_lap",
        "degradation_iqr_s", "median_pace_s", "max_stint_laps",
    ),
}


def _replace(conn: Connection, table: str, session_id: int, frame: pl.DataFrame) -> None:
    """Delete this session's rows for the current engine version, then insert."""
    # Table names come from the module-level mapping above, never from user input.
    conn.execute(
        text(f"DELETE FROM {table} WHERE session_id = :s AND engine_version = :v"),  # noqa: S608
        {"s": session_id, "v": ENGINE_VERSION},
    )
    if frame.is_empty():
        return

    columns = _COLUMNS[table]
    # "rank" is a reserved word in SQL, so every identifier is quoted.
    column_list = ", ".join(f'"{name}"' for name in (*columns, "engine_version"))
    placeholders = ", ".join(f":{name}" for name in (*columns, "engine_version"))
    rows = [
        {**{name: row.get(name) for name in columns}, "engine_version": ENGINE_VERSION}
        for row in frame.to_dicts()
    ]
    conn.execute(
        text(f"INSERT INTO {table} ({column_list}) VALUES ({placeholders})"),  # noqa: S608
        rows,
    )
