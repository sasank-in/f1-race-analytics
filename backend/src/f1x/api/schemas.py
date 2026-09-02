"""Response models.

These are the contract. The OpenAPI schema is generated from them and the frontend's
TypeScript client is generated from that, so a field renamed here propagates to the UI
as a compile error rather than as a silently missing value at runtime.

Two conventions matter throughout:

**Units are in the field name.** ``pace_s`` and ``gap_to_best_s`` are seconds;
``degradation_s_per_lap`` is seconds per lap. A bare ``pace`` invites the reader to
guess, and the guess is eventually wrong.

**Estimates carry their own caveats.** Where a value is modelled rather than measured —
degradation slopes, fuel-corrected times, pit loss — the response says so. A number that
travels without its provenance gets treated as fact by whoever receives it.
"""

from __future__ import annotations

import datetime as dt

from pydantic import BaseModel, Field


class Meta(BaseModel):
    """Provenance attached to every analysis response."""

    engine_version: str = Field(description="Engine version that produced these values")
    computed_at: dt.datetime | None = Field(
        default=None, description="When the underlying metrics were computed"
    )


# --------------------------------------------------------------------------
# reference
# --------------------------------------------------------------------------


class SeasonOut(BaseModel):
    year: int
    n_events: int
    has_telemetry: bool = Field(
        description="Whether car telemetry exists for this season (2018 onward)"
    )


class EventOut(BaseModel):
    id: int
    season_year: int
    round: int
    name: str
    country: str | None = None
    circuit_key: str | None = None
    event_date: dt.date | None = None


class SessionOut(BaseModel):
    id: int
    event_id: int
    kind: str = Field(description="FP1-3, Q, SQ, S or R")
    event_name: str
    season_year: int
    round: int
    total_laps: int | None = None
    telemetry_loaded: bool
    start_utc: dt.datetime | None = None


# --------------------------------------------------------------------------
# laps and pace
# --------------------------------------------------------------------------


class LapOut(BaseModel):
    driver_number: str
    lap_number: int
    lap_time_s: float | None = None
    compound: str | None = None
    tyre_life: float | None = None
    stint: int | None = None
    position: float | None = None
    is_representative: bool | None = Field(
        default=None, description="Whether this lap belongs in a pace sample"
    )
    exclusion_reason: str | None = Field(
        default=None, description="Why the lap was excluded, when it was"
    )
    fuel_corrected_s: float | None = Field(
        default=None,
        description="Lap time corrected to an empty-tank equivalent using a published "
        "0.030 s/kg coefficient. An estimate, not measured fuel telemetry.",
    )


class PaceOut(BaseModel):
    driver_number: str
    rank: int
    n_laps: int
    pace_s: float = Field(
        description="20th percentile of clean fuel-corrected laps, not the fastest lap"
    )
    gap_to_best_s: float
    best_s: float
    median_s: float
    std_s: float = Field(description="Lap-time spread: lower is more consistent")
    clean_air_laps: int


class PaceResponse(BaseModel):
    session_id: int
    meta: Meta
    drivers: list[PaceOut]


# --------------------------------------------------------------------------
# degradation
# --------------------------------------------------------------------------


class StintFitOut(BaseModel):
    driver_number: str
    stint: int
    compound: str | None = None
    n_laps: int
    pace_s: float = Field(description="Fitted lap time at zero tyre age")
    degradation_s_per_lap: float = Field(
        description="Fitted slope. May be negative when the stint was too short to "
        "support an estimate; see is_physical."
    )
    is_physical: bool | None = Field(
        default=None,
        description="False when the fitted slope is negative. Tyres do not gain time "
        "with age, so such a fit means the stint gave too little usable range.",
    )
    is_reliable: bool | None = None
    r_squared: float | None = None
    tyre_age_range: float | None = Field(
        default=None, description="Span of tyre age the fit actually covered"
    )
    excluded_lap_count: int | None = Field(
        default=None, description="Laps the warm-up cutoff removed before fitting"
    )


class DegradationOut(BaseModel):
    compound: str
    n_stints: int
    n_laps: int
    degradation_s_per_lap: float = Field(
        description="Median slope across reliable stints on this compound"
    )
    degradation_iqr_s: float = Field(
        description="Interquartile spread: the honest width of the estimate"
    )
    median_pace_s: float
    max_stint_laps: int = Field(
        description="Longest observed stint. Predictions beyond this extrapolate."
    )


class DegradationResponse(BaseModel):
    session_id: int
    meta: Meta
    compounds: list[DegradationOut]
    stints: list[StintFitOut]


# --------------------------------------------------------------------------
# strategy
# --------------------------------------------------------------------------


class PitLossOut(BaseModel):
    n_stops: int
    pit_window_s: float = Field(description="Pit entry to pit exit: transit only")
    reference_lap_s: float
    net_loss_s: float = Field(
        description="What a stop actually costs: how much the in-lap and out-lap "
        "together exceed two normal laps"
    )
    spread_s: float


class StrategyOptionOut(BaseModel):
    n_stops: int
    stint_lengths: list[int]
    degradation_cost_s: float
    pit_cost_s: float
    total_cost_s: float


class StrategyResponse(BaseModel):
    session_id: int
    meta: Meta
    pit_loss: PitLossOut
    options: list[StrategyOptionOut] = Field(
        description="Ranked cheapest first. A dry race mandates at least one stop."
    )


class UndercutWindowOut(BaseModel):
    """One lap where a driver was close enough behind to try an undercut."""

    lap_number: int
    attacker: str
    defender: str
    gap_s: float
    gain_per_lap_s: float = Field(
        description="Per-lap advantage a fresh set gives over the rival's worn tyres"
    )
    total_gain_s: float
    margin_s: float = Field(
        description="How much the undercut wins or misses by. Negative means it fails."
    )
    verdict: str = Field(description="undercut, hold, or marginal")


class UndercutResponse(BaseModel):
    session_id: int
    meta: Meta
    degradation_s_per_lap: float
    net_pit_loss_s: float
    windows: list[UndercutWindowOut]


class StintOut(BaseModel):
    """One run on a set of tyres, for the timeline."""

    driver_number: str
    stint: int
    compound: str | None = None
    start_lap: int
    end_lap: int
    n_laps: int
    tyre_age_start: float | None = None
    fresh_tyre: bool | None = None


class StintTimelineResponse(BaseModel):
    session_id: int
    total_laps: int | None = None
    stints: list[StintOut]


# --------------------------------------------------------------------------
# telemetry
# --------------------------------------------------------------------------


class CornerOut(BaseModel):
    index: int
    apex_distance_m: float
    min_speed_kmh: float
    entry_speed_kmh: float
    exit_speed_kmh: float
    braking_point_m: float | None = None
    throttle_point_m: float | None = None


class CornerDeltaOut(BaseModel):
    index: int
    apex_distance_m: float
    reference_min_speed_kmh: float
    comparison_min_speed_kmh: float
    delta_kmh: float


class TelemetryCompareResponse(BaseModel):
    session_id: int
    meta: Meta
    reference_driver: str
    comparison_driver: str
    final_delta_s: float = Field(
        description="Lap-time difference. Negative means the comparison lap was quicker."
    )
    distance_m: list[float] = Field(description="Common distance grid, one metre apart")
    delta_s: list[float] = Field(
        description="Cumulative time difference at each point on the lap"
    )
    corners: list[CornerDeltaOut]


# --------------------------------------------------------------------------
# simulation
# --------------------------------------------------------------------------


class SimulatedStrategyOut(BaseModel):
    n_stops: int
    stint_lengths: list[int]
    median_s: float
    p5_s: float
    p95_s: float
    spread_s: float = Field(description="Width of the middle 90 % of outcomes")
    win_rate: float = Field(description="Share of simulated races this strategy won")


class SimulationResponse(BaseModel):
    session_id: int
    meta: Meta
    iterations: int
    safety_car_rate: float
    is_decisive: bool = Field(
        description="False when the leading strategy wins too rarely to call it"
    )
    strategies: list[SimulatedStrategyOut]


# --------------------------------------------------------------------------
# ratings
# --------------------------------------------------------------------------


class DriverRatingOut(BaseModel):
    driver_number: str
    rank: int
    n_races: int
    overall: float
    pace: float
    racecraft: float
    consistency: float
    tyre_management: float
    strongest: str


class RatingsResponse(BaseModel):
    season: int
    meta: Meta
    note: str = Field(
        default="Scores are relative to this season's field, not an absolute scale.",
        description="Ratings are min-max normalised, so cross-season comparison is "
        "meaningless",
    )
    drivers: list[DriverRatingOut]


# --------------------------------------------------------------------------
# track map
# --------------------------------------------------------------------------


class TrackPointOut(BaseModel):
    """One sample of the racing line, with the speed carried there."""

    x: float = Field(description="Track coordinate, normalised to 0-1000")
    y: float
    speed_kmh: float
    distance_m: float


class TrackMapResponse(BaseModel):
    session_id: int
    meta: Meta
    driver_number: str
    lap_number: int
    lap_time_s: float | None = None
    lap_distance_m: float = Field(
        description="Integrated from speed, so within a percent or two of the official length"
    )
    min_speed_kmh: float
    max_speed_kmh: float
    points: list[TrackPointOut]
    corners: list[CornerOut] = Field(
        description="Detected corners, with the map coordinate of each apex"
    )


class CornerOnMapOut(CornerOut):
    """A corner with its position on the map, so the table links to the geometry."""

    x: float
    y: float


# --------------------------------------------------------------------------
# teammates
# --------------------------------------------------------------------------


class TeammateDeltaOut(BaseModel):
    """One pairing's head-to-head over the sessions they both completed."""

    team_key: str | None = None
    driver_a: str
    driver_b: str
    n_sessions: int
    faster_driver: str
    margin_s: float = Field(
        description="Median pace gap between the pair, in seconds per lap"
    )
    median_delta_s: float = Field(
        description="Signed gap: negative means driver_a was quicker"
    )
    std_delta_s: float = Field(
        description="Spread across sessions. A small margin with a large spread is not settled."
    )
    sessions_a_ahead: int
    sessions_b_ahead: int
    is_decisive: bool = Field(
        description="True when one driver led at least 70% of shared sessions, not just on average"
    )
    is_reliable: bool


class TeammatesResponse(BaseModel):
    season: int
    meta: Meta
    note: str = Field(
        default="Same car, so the difference is mostly the driver — but strategy "
        "splits, damage and traffic are not controlled for.",
    )
    pairings: list[TeammateDeltaOut]
