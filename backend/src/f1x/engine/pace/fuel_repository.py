"""Database boundary for cross-season fuel fitting."""

from __future__ import annotations

import polars as pl
from sqlalchemy import Engine, text

from f1x.engine.pace.fuel_fit import CircuitFuelFit, fit_circuit

# Laps for one circuit across every season present, with the season attached so the
# fit can absorb year-to-year pace differences.
CIRCUIT_LAPS_QUERY = """
    SELECT e.season_year,
           m.driver_number,
           m.lap_number,
           m.stint,
           m.tyre_life,
           m.fuel_load_kg,
           m.lap_time_s,
           m.is_representative
    FROM mart.lap_metrics m
    JOIN core.sessions s ON s.id = m.session_id
    JOIN core.events e ON e.id = s.event_id
    JOIN core.circuits c ON c.id = e.circuit_id
    WHERE c.key = :circuit_key
    ORDER BY e.season_year, m.driver_number, m.lap_number
"""

CIRCUITS_QUERY = """
    SELECT c.key, count(DISTINCT e.season_year) AS seasons
    FROM core.circuits c
    JOIN core.events e ON e.circuit_id = c.id
    GROUP BY 1
    ORDER BY 1
"""


def load_circuit_laps(engine: Engine, circuit_key: str) -> pl.DataFrame:
    """Read every lap recorded at one circuit, across all seasons."""
    with engine.connect() as conn:
        rows = conn.execute(text(CIRCUIT_LAPS_QUERY), {"circuit_key": circuit_key})
        records = [dict(row) for row in rows.mappings()]
    return pl.DataFrame(records) if records else pl.DataFrame()


def fit_all_circuits(engine: Engine) -> list[CircuitFuelFit]:
    """Fit the fuel effect for every circuit with enough history.

    Circuits with a single season are still returned, carrying the default coefficient
    and the reason they could not be fitted, so the coverage gap is visible rather
    than silently absent.
    """
    with engine.connect() as conn:
        circuits = [row[0] for row in conn.execute(text(CIRCUITS_QUERY))]

    return [
        fit_circuit(load_circuit_laps(engine, circuit), circuit_key=circuit)
        for circuit in circuits
    ]
