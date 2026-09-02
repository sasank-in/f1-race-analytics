"""Strategy, simulation and ratings endpoints.

These compute on request rather than reading a mart, because they take parameters a
caller chooses — iteration counts, driver pairs — so precomputing every combination
is not possible. The Redis cache keyed on those parameters is what keeps them cheap
on repeat.
"""

from __future__ import annotations

from typing import Any

import polars as pl
from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import text

from f1x.api.deps import ResponseCache, get_cache, get_engine
from f1x.api.schemas import (
    DriverRatingOut,
    Meta,
    PitLossOut,
    RatingsResponse,
    SimulatedStrategyOut,
    SimulationResponse,
    StintOut,
    StintTimelineResponse,
    StrategyOptionOut,
    StrategyResponse,
    UndercutResponse,
    UndercutWindowOut,
)
from f1x.config import ENGINE_VERSION

router = APIRouter(tags=["strategy"])


def _frame(sql: str, params: dict[str, Any]) -> pl.DataFrame:
    with get_engine().connect() as conn:
        rows = [dict(r) for r in conn.execute(text(sql), params).mappings()]
    return pl.DataFrame(rows) if rows else pl.DataFrame()


def _session_inputs(session_id: int) -> tuple[pl.DataFrame, pl.DataFrame, int, float, float]:
    """Everything strategy and simulation need, or a 404 explaining what is missing."""
    laps = _frame(
        "SELECT * FROM mart.lap_metrics WHERE session_id = :s AND engine_version = :v",
        {"s": session_id, "v": ENGINE_VERSION},
    )
    if laps.is_empty():
        raise HTTPException(
            status_code=404,
            detail=(
                f"no lap metrics for session {session_id}. "
                "Run `f1x transform` and `f1x analyse` first."
            ),
        )

    stops = _frame(
        "SELECT * FROM core.pit_stops WHERE session_id = :s", {"s": session_id}
    )
    with get_engine().connect() as conn:
        total_laps = conn.execute(
            text("SELECT total_laps FROM core.sessions WHERE id = :s"), {"s": session_id}
        ).scalar_one_or_none()
        degradation = conn.execute(
            text(
                "SELECT degradation_s_per_lap FROM mart.degradation_curves "
                "WHERE session_id = :s AND engine_version = :v "
                "ORDER BY n_stints DESC LIMIT 1"
            ),
            {"s": session_id, "v": ENGINE_VERSION},
        ).scalar_one_or_none()
        base_lap = conn.execute(
            text(
                "SELECT percentile_cont(0.5) WITHIN GROUP (ORDER BY lap_time_s) "
                "FROM mart.lap_metrics "
                "WHERE session_id = :s AND engine_version = :v AND is_representative"
            ),
            {"s": session_id, "v": ENGINE_VERSION},
        ).scalar_one_or_none()

    if not total_laps or degradation is None or base_lap is None:
        raise HTTPException(
            status_code=404,
            detail=f"session {session_id} lacks a degradation model or race distance",
        )
    return laps, stops, int(total_laps), float(degradation), float(base_lap)


@router.get("/strategy/{session_id}", response_model=StrategyResponse)
def get_strategy(session_id: int) -> StrategyResponse:
    """Pit loss and ranked stop strategies.

    Pit loss is measured from the laps either side of a stop, not from pit-lane
    transit time: the cost of stopping is how much the in-lap and out-lap together
    exceed two normal laps.
    """
    cache = get_cache()
    key = ResponseCache.key("strategy", {"session_id": session_id})
    if (hit := cache.get(key)) is not None:
        return StrategyResponse(**hit)

    from f1x.engine.strategy.optimiser import MAX_STOPS, optimise
    from f1x.engine.strategy.pit_loss import estimate_from_laps

    laps, stops, total_laps, degradation, _ = _session_inputs(session_id)
    loss = estimate_from_laps(stops, laps, session_id=session_id)
    if loss is None:
        raise HTTPException(
            status_code=404,
            detail=f"session {session_id} has too few clean pit stops to estimate loss",
        )

    ranked = optimise(
        total_laps=total_laps,
        slope_s_per_lap=degradation,
        net_pit_loss_s=loss.net_loss_s,
        max_stops=MAX_STOPS,
    )
    response = StrategyResponse(
        session_id=session_id,
        meta=Meta(engine_version=ENGINE_VERSION),
        pit_loss=PitLossOut(
            n_stops=loss.n_stops,
            pit_window_s=loss.pit_window_s,
            reference_lap_s=loss.on_track_equivalent_s,
            net_loss_s=loss.net_loss_s,
            spread_s=loss.spread_s,
        ),
        options=[
            StrategyOptionOut(
                n_stops=o.n_stops,
                stint_lengths=list(o.stint_lengths),
                degradation_cost_s=o.degradation_cost_s,
                pit_cost_s=o.pit_cost_s,
                total_cost_s=o.total_cost_s,
            )
            for o in ranked
        ],
    )
    cache.set(key, response.model_dump())
    return response


@router.get("/undercut/{session_id}", response_model=UndercutResponse)
def get_undercut(
    session_id: int,
    max_gap_s: float = Query(default=3.0, ge=0.5, le=10.0),
) -> UndercutResponse:
    """Every lap where a driver was close enough behind to try an undercut.

    The arithmetic is a race between two quantities: pitting costs the net pit loss
    and gains the difference between fresh-tyre pace and the rival's degraded pace,
    compounded over the laps before they respond. Calls inside half a second either
    way are reported as marginal rather than decided.
    """
    cache = get_cache()
    key = ResponseCache.key(
        "undercut", {"session_id": session_id, "max_gap_s": max_gap_s}
    )
    if (hit := cache.get(key)) is not None:
        return UndercutResponse(**hit)

    from f1x.engine.strategy.pit_loss import estimate_from_laps
    from f1x.engine.strategy.undercut import scan_session

    laps, stops, _, degradation, _ = _session_inputs(session_id)
    loss = estimate_from_laps(stops, laps, session_id=session_id)
    if loss is None:
        raise HTTPException(
            status_code=404,
            detail=f"session {session_id} has too few clean pit stops to model undercuts",
        )

    windows = scan_session(
        laps,
        session_id=session_id,
        degradation_s_per_lap=degradation,
        net_pit_loss_s=loss.net_loss_s,
        max_gap_s=max_gap_s,
    )
    response = UndercutResponse(
        session_id=session_id,
        meta=Meta(engine_version=ENGINE_VERSION),
        degradation_s_per_lap=degradation,
        net_pit_loss_s=loss.net_loss_s,
        windows=[
            UndercutWindowOut(
                lap_number=w.lap_number,
                attacker=w.attacker,
                defender=w.defender,
                gap_s=w.gap_s,
                gain_per_lap_s=w.gain_per_lap_s,
                total_gain_s=w.total_gain_s,
                margin_s=w.margin_s,
                verdict=w.verdict,
            )
            for w in windows
        ],
    )
    cache.set(key, response.model_dump())
    return response


@router.get("/stints/{session_id}", response_model=StintTimelineResponse)
def get_stints(session_id: int) -> StintTimelineResponse:
    """Tyre stints per driver, for a strategy timeline."""
    with get_engine().connect() as conn:
        total_laps = conn.execute(
            text("SELECT total_laps FROM core.sessions WHERE id = :s"), {"s": session_id}
        ).scalar_one_or_none()
        rows = conn.execute(
            text(
                "SELECT driver_number, stint, compound::text AS compound, start_lap, "
                "       end_lap, n_laps, tyre_age_start, fresh_tyre "
                "FROM core.stints WHERE session_id = :s "
                "ORDER BY driver_number, stint"
            ),
            {"s": session_id},
        ).mappings()
        stints = [StintOut(**dict(row)) for row in rows]

    if not stints:
        raise HTTPException(
            status_code=404,
            detail=(
                f"no stints for session {session_id}. Run `f1x transform` first."
            ),
        )
    return StintTimelineResponse(
        session_id=session_id, total_laps=total_laps, stints=stints
    )


@router.get("/simulate/{session_id}", response_model=SimulationResponse)
def simulate_race(
    session_id: int,
    iterations: int = Query(default=2000, ge=100, le=20000),
) -> SimulationResponse:
    """Compare strategies by simulating the race many times.

    A single "four seconds faster" is misleading; what matters is how often a strategy
    wins once safety cars and lap-time noise are resampled. All strategies share one
    seed so they face the same sampled races.
    """
    cache = get_cache()
    key = ResponseCache.key(
        "simulate", {"session_id": session_id, "iterations": iterations}
    )
    if (hit := cache.get(key)) is not None:
        return SimulationResponse(**hit)

    from f1x.engine.simulation.race import RaceConditions, compare_strategies
    from f1x.engine.strategy.optimiser import MAX_STOPS, split_evenly
    from f1x.engine.strategy.pit_loss import estimate_from_laps

    laps, stops, total_laps, degradation, base_lap = _session_inputs(session_id)
    loss = estimate_from_laps(stops, laps, session_id=session_id)
    if loss is None:
        raise HTTPException(
            status_code=404,
            detail=f"session {session_id} has too few clean pit stops to simulate",
        )

    conditions = RaceConditions(
        total_laps=total_laps,
        base_lap_s=base_lap,
        net_pit_loss_s=loss.net_loss_s,
        degradation_s_per_lap=degradation,
    )
    comparison = compare_strategies(
        conditions,
        [split_evenly(total_laps, n + 1) for n in range(1, MAX_STOPS)],
        iterations=iterations,
        seed=42,
    )
    response = SimulationResponse(
        session_id=session_id,
        meta=Meta(engine_version=ENGINE_VERSION),
        iterations=iterations,
        safety_car_rate=comparison.results[0].safety_car_rate if comparison.results else 0.0,
        is_decisive=comparison.is_decisive,
        strategies=[
            SimulatedStrategyOut(
                n_stops=r.n_stops,
                stint_lengths=list(r.stint_lengths),
                median_s=r.median_s,
                p5_s=r.p5_s,
                p95_s=r.p95_s,
                spread_s=r.spread_s,
                win_rate=comparison.win_rates[r.n_stops],
            )
            for r in sorted(comparison.results, key=lambda x: x.median_s)
        ],
    )
    cache.set(key, response.model_dump())
    return response


@router.get("/ratings/{season}", response_model=RatingsResponse)
def get_ratings(season: int) -> RatingsResponse:
    """Composite driver ratings for one season.

    Scores are min-max normalised across the field being compared, so they rank
    drivers within a season and carry no meaning across seasons.
    """
    cache = get_cache()
    key = ResponseCache.key("ratings", {"season": season})
    if (hit := cache.get(key)) is not None:
        return RatingsResponse(**hit)

    from f1x.engine.metrics.ratings import build_ratings

    pace = _frame(
        "SELECT p.* FROM mart.pace_rankings p "
        "JOIN core.sessions s ON s.id = p.session_id "
        "JOIN core.events e ON e.id = s.event_id "
        "WHERE e.season_year = :season AND p.engine_version = :v",
        {"season": season, "v": ENGINE_VERSION},
    )
    if pace.is_empty():
        raise HTTPException(
            status_code=404,
            detail=f"no pace rankings for {season}. Run `f1x analyse all` first.",
        )

    results = _frame(
        "SELECT en.driver_number, r.grid_position, r.position FROM core.results r "
        "JOIN core.sessions s ON s.id = r.session_id "
        "JOIN core.events e ON e.id = s.event_id "
        "JOIN core.entries en "
        "  ON en.session_id = r.session_id AND en.driver_id = r.driver_id "
        "WHERE e.season_year = :season",
        {"season": season},
    )
    stints = _frame(
        "SELECT f.* FROM mart.stint_fits f "
        "JOIN core.sessions s ON s.id = f.session_id "
        "JOIN core.events e ON e.id = s.event_id "
        "WHERE e.season_year = :season AND f.engine_version = :v",
        {"season": season, "v": ENGINE_VERSION},
    )

    ratings = build_ratings(pace, results, stints)
    response = RatingsResponse(
        season=season,
        meta=Meta(engine_version=ENGINE_VERSION),
        drivers=[
            DriverRatingOut(
                driver_number=r.driver_number,
                rank=r.rank,
                n_races=r.n_races,
                overall=r.overall,
                pace=r.pace,
                racecraft=r.racecraft,
                consistency=r.consistency,
                tyre_management=r.tyre_management,
                strongest=r.strongest,
            )
            for r in ratings
        ],
    )
    cache.set(key, response.model_dump())
    return response
