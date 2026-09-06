"""On-demand fetching of a race that is not in the database yet.

Until now the dataset was whatever had been backfilled from the command line, so
analysing a new race meant leaving the application. This module lets a race be pulled
from the Live Timing archive while the API is running.

Two things shape the design.

*Fetching is not enough.* A race is only viewable after ingest, transform and analyse
have all run — ingestion alone writes `core` rows that no page reads. So a job runs the
whole pipeline and is not reported complete until the race can actually be opened.

*It is far too slow for a request.* A race with telemetry takes minutes and moves tens
of millions of rows. The work therefore runs in a background thread and the caller polls
for progress, rather than holding a connection open and timing out halfway through.

Job state is in memory: restarting the API forgets the history, which is the right
trade for a single-node analysis tool. The *data* is durable — it is in PostgreSQL the
moment a stage commits — and a forgotten job only means losing the progress record, not
the race. A queue would buy durability at the cost of a broker to run, and nothing here
needs one.
"""

from __future__ import annotations

import itertools
import logging
import threading
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum

logger = logging.getLogger(__name__)


class JobState(StrEnum):
    """Where a fetch has got to. A string enum so it serialises as a readable name."""

    QUEUED = "queued"
    INGESTING = "ingesting"
    TRANSFORMING = "transforming"
    ANALYSING = "analysing"
    COMPLETE = "complete"
    FAILED = "failed"


#: Rough share of total time each stage takes, for a progress figure that does not
#: stall at 30% for four minutes. Ingestion genuinely dominates: it is network-bound
#: and writes every telemetry row, while transform and analyse read back one session.
_STAGE_PROGRESS: dict[JobState, float] = {
    JobState.QUEUED: 0.0,
    JobState.INGESTING: 0.05,
    JobState.TRANSFORMING: 0.80,
    JobState.ANALYSING: 0.90,
    JobState.COMPLETE: 1.0,
    JobState.FAILED: 1.0,
}


@dataclass
class FetchJob:
    """One race being pulled from the archive, and how far along it is."""

    id: str
    year: int
    round_number: int
    kind: str
    telemetry: bool
    #: Creation order. Wall-clock time is not enough to sort by: two jobs created in
    #: the same microsecond compare equal and the "newest first" listing flips.
    seq: int = 0
    state: JobState = JobState.QUEUED
    session_id: int | None = None
    detail: str = "Waiting to start"
    error: str | None = None
    warnings: list[str] = field(default_factory=list)
    laps: int = 0
    telemetry_samples: int = 0
    started_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    finished_at: datetime | None = None

    @property
    def progress(self) -> float:
        return _STAGE_PROGRESS[self.state]

    @property
    def is_terminal(self) -> bool:
        return self.state in (JobState.COMPLETE, JobState.FAILED)


class FetchRegistry:
    """Thread-safe store of fetch jobs, keyed by id.

    Every method takes the lock: jobs are mutated on worker threads and read on the
    request thread, so an unsynchronised read can otherwise observe a half-written job.
    """

    #: Keep the recent history bounded. A user fetching races one at a time will never
    #: reach this, and it stops a long-lived process from growing without limit.
    _MAX_JOBS = 64

    def __init__(self) -> None:
        self._jobs: dict[str, FetchJob] = {}
        self._lock = threading.Lock()
        # itertools.count().__next__ is atomic under the GIL, and it is taken inside
        # the lock regardless, so ordering is total even under concurrent creation.
        self._seq = itertools.count()

    def create(self, year: int, round_number: int, kind: str, telemetry: bool) -> FetchJob:
        with self._lock:
            job = FetchJob(
                id=uuid.uuid4().hex[:12],
                year=year,
                round_number=round_number,
                kind=kind,
                telemetry=telemetry,
                seq=next(self._seq),
            )
            self._evict_locked()
            self._jobs[job.id] = job
        return job

    def get(self, job_id: str) -> FetchJob | None:
        with self._lock:
            return self._jobs.get(job_id)

    def all(self) -> list[FetchJob]:
        """Newest first, so a UI can show the most recent fetch without sorting.

        Named ``all`` rather than ``list``: inside the class body a method named
        ``list`` shadows the builtin, and every ``list[str]`` annotation below it
        stops resolving.
        """
        with self._lock:
            return sorted(self._jobs.values(), key=lambda j: j.seq, reverse=True)

    def find_active(self, year: int, round_number: int, kind: str) -> FetchJob | None:
        """An unfinished job for the same race, if one exists.

        Ingesting the same race twice concurrently would have both threads writing the
        same rows, so the router returns the running job instead of starting a second.
        """
        with self._lock:
            for job in self._jobs.values():
                if (
                    not job.is_terminal
                    and job.year == year
                    and job.round_number == round_number
                    and job.kind == kind
                ):
                    return job
        return None

    def update(
        self,
        job_id: str,
        *,
        state: JobState | None = None,
        detail: str | None = None,
        session_id: int | None = None,
        laps: int | None = None,
        telemetry_samples: int | None = None,
        warnings: list[str] | None = None,
        error: str | None = None,
    ) -> None:
        """Apply a partial update. Named fields rather than ``**kwargs`` so a typo is
        a type error here instead of silently creating an attribute nothing reads."""
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                return
            if state is not None:
                job.state = state
            if detail is not None:
                job.detail = detail
            if session_id is not None:
                job.session_id = session_id
            if laps is not None:
                job.laps = laps
            if telemetry_samples is not None:
                job.telemetry_samples = telemetry_samples
            if warnings is not None:
                job.warnings = warnings
            if error is not None:
                job.error = error
            if job.is_terminal and job.finished_at is None:
                job.finished_at = datetime.now(UTC)

    def _evict_locked(self) -> None:
        """Drop the oldest finished jobs. Never evicts one still running."""
        if len(self._jobs) < self._MAX_JOBS:
            return
        finished = sorted(
            (j for j in self._jobs.values() if j.is_terminal),
            key=lambda j: j.seq,
        )
        for job in finished[: len(self._jobs) - self._MAX_JOBS + 1]:
            del self._jobs[job.id]


#: Process-wide registry. The API holds one; the CLI does not use it.
registry = FetchRegistry()


def run_fetch(job_id: str, on_finish: Callable[[], None] | None = None) -> None:
    """Run ingest, transform and analyse for one job, recording progress as it goes.

    Every failure is caught and recorded on the job. This runs on a background thread
    with nothing to propagate to, so an escaping exception would kill the thread
    silently and leave the job stuck reporting "ingesting" for ever.
    """
    from sqlalchemy import create_engine

    from f1x.config import get_settings
    from f1x.engine.repository import analyse_and_store
    from f1x.ingest import FastF1Client, SessionRequest
    from f1x.ingest.loader import SessionLoader
    from f1x.repo import create_session_factory
    from f1x.transform.repository import transform_and_store

    job = registry.get(job_id)
    if job is None:  # Evicted before the thread started; nothing to do.
        return

    settings = get_settings()
    label = f"{job.year} round {job.round_number} {job.kind}"

    try:
        registry.update(
            job_id,
            state=JobState.INGESTING,
            detail=f"Downloading {label} from the Live Timing archive",
        )
        request = SessionRequest(job.year, job.round_number, job.kind, telemetry=job.telemetry)

        engine, sessions = create_session_factory(settings)
        try:
            source = FastF1Client(settings).load(request)
            summary = SessionLoader(sessions).persist(request, source)
        finally:
            engine.dispose()

        registry.update(
            job_id,
            session_id=summary.session_id,
            laps=summary.laps,
            telemetry_samples=summary.telemetry,
            warnings=list(summary.quality.warnings),
            state=JobState.TRANSFORMING,
            detail=f"Deriving corrections and stints from {summary.laps:,} laps",
        )

        # A separate engine from the ingest one, which has been disposed. Both are
        # short-lived by design: a fetch may sit idle for minutes between stages.
        engine = create_engine(str(settings.database_url), pool_pre_ping=True)
        try:
            transform_and_store(engine, summary.session_id)
            registry.update(
                job_id,
                state=JobState.ANALYSING,
                detail="Fitting pace and degradation models",
            )
            analyse_and_store(engine, summary.session_id)
        finally:
            engine.dispose()

        registry.update(
            job_id,
            state=JobState.COMPLETE,
            detail=f"{label} is ready to analyse",
        )
        logger.info("fetch %s complete: session %s", job_id, summary.session_id)

    except Exception as exc:  # Thread boundary: nothing to propagate to.
        logger.exception("fetch %s failed", job_id)
        registry.update(
            job_id,
            state=JobState.FAILED,
            error=str(exc),
            detail=f"Could not fetch {label}",
        )
    finally:
        if on_finish is not None:
            on_finish()


def start_fetch(year: int, round_number: int, kind: str, telemetry: bool) -> FetchJob:
    """Create a job and run it on a daemon thread.

    Daemon so a fetch cannot keep the API process alive on shutdown. An interrupted
    fetch leaves whatever stages already committed, and re-fetching replaces them.
    """
    job = registry.create(year, round_number, kind, telemetry)
    thread = threading.Thread(
        target=run_fetch,
        args=(job.id,),
        name=f"fetch-{job.id}",
        daemon=True,
    )
    thread.start()
    return job
