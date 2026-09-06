"""Browse the published calendar and pull a race in on demand.

The rest of the API is a read-only projection of what has been ingested. This router is
the exception: it reaches the archive directly, so the dataset is no longer limited to
whatever was backfilled from a terminal.

Fetching is asynchronous. A race with telemetry takes minutes, so `POST /fetch` starts
a background job and returns immediately; the caller polls `GET /fetch/{id}` to watch
it move through ingest, transform and analyse.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query

from f1x.api.deps import get_engine
from f1x.api.schemas import (
    FetchJobOut,
    FetchRequest,
    ScheduledRaceOut,
    ScheduleResponse,
)
from f1x.config import get_settings
from f1x.ingest.exceptions import IngestionError
from f1x.ingest.jobs import FetchJob, JobState, registry, start_fetch
from f1x.ingest.schedule import annotate_ingested, fetch_schedule

router = APIRouter(tags=["fetch"])

VALID_KINDS = {"FP1", "FP2", "FP3", "Q", "SQ", "S", "R"}


def _as_out(job: FetchJob) -> FetchJobOut:
    return FetchJobOut(
        id=job.id,
        year=job.year,
        round=job.round_number,
        kind=job.kind,
        telemetry=job.telemetry,
        state=job.state.value,
        detail=job.detail,
        progress=job.progress,
        session_id=job.session_id,
        laps=job.laps,
        telemetry_samples=job.telemetry_samples,
        warnings=job.warnings,
        error=job.error,
        started_at=job.started_at,
        finished_at=job.finished_at,
    )


@router.get("/schedule/{season}", response_model=ScheduleResponse)
def get_schedule(season: int) -> ScheduleResponse:
    """The published calendar for a season, marking what is already stored.

    Works for a season that has never been ingested — that is how a client discovers
    races worth fetching.
    """
    first = get_settings().telemetry_first_season
    if season < first:
        raise HTTPException(
            400,
            f"lap timing starts in {first}; earlier seasons cannot be analysed",
        )

    try:
        races = fetch_schedule(season, get_settings())
    except IngestionError as exc:
        # The archive is the dependency here, not the database, so 502 rather than 500:
        # the request was valid and the failure is upstream.
        raise HTTPException(502, str(exc)) from exc

    races = annotate_ingested(races, get_engine())
    return ScheduleResponse(
        season=season,
        races=[
            ScheduledRaceOut(
                season=race.season,
                round=race.round_number,
                name=race.name,
                country=race.country,
                event_date=race.event_date,
                has_run=race.has_run,
                is_ingested=race.is_ingested,
                session_id=race.session_id,
            )
            for race in races
        ],
    )


@router.post("/fetch", response_model=FetchJobOut, status_code=202)
def start_fetch_job(request: FetchRequest) -> FetchJobOut:
    """Fetch one session from the archive and run it through the full pipeline.

    Returns 202 with a job to poll. Re-fetching a race already stored is allowed and
    replaces it — that is how a session ingested without telemetry gains its traces.
    """
    kind = request.kind.upper()
    if kind not in VALID_KINDS:
        raise HTTPException(400, f"unsupported session kind: {request.kind}")

    settings = get_settings()
    # Lap timing itself begins in 2018. Before that a session loads with no laps at
    # all, so it would ingest successfully and then analyse to nothing — refuse it
    # here rather than store a race no page can render.
    if request.year < settings.telemetry_first_season:
        raise HTTPException(
            400,
            f"lap timing is only available from {settings.telemetry_first_season}; "
            f"{request.year} has results but no laps, which the engine needs",
        )

    # Two threads ingesting one race would write the same rows concurrently. Hand back
    # the running job instead, which is also what a user double-clicking wants.
    existing = registry.find_active(request.year, request.round, kind)
    if existing is not None:
        return _as_out(existing)

    job = start_fetch(request.year, request.round, kind, request.telemetry)
    return _as_out(job)


@router.get("/fetch", response_model=list[FetchJobOut])
def list_fetch_jobs(
    limit: int = Query(default=20, ge=1, le=64),
) -> list[FetchJobOut]:
    """Recent fetches, newest first. In-memory, so empty after a restart."""
    return [_as_out(job) for job in registry.all()[:limit]]


@router.get("/fetch/{job_id}", response_model=FetchJobOut)
def get_fetch_job(job_id: str) -> FetchJobOut:
    """Poll one fetch.

    A 404 after a restart means the job record was lost, not that the race was — check
    the schedule, which reads the database rather than this registry.
    """
    job = registry.get(job_id)
    if job is None:
        raise HTTPException(404, f"no fetch job {job_id}; it may have expired")
    return _as_out(job)


__all__ = ["JobState", "router"]
