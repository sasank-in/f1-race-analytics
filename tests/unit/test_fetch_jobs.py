"""Fetch-job registry and pipeline orchestration.

The registry is the only shared mutable state in the application, written from worker
threads and read from request threads, so these tests cover the concurrency rules as
well as the state machine.
"""

from __future__ import annotations

import threading

import pytest

from f1x.ingest.jobs import FetchRegistry, JobState, run_fetch


@pytest.fixture
def registry() -> FetchRegistry:
    return FetchRegistry()


def test_new_job_starts_queued(registry: FetchRegistry) -> None:
    job = registry.create(2024, 1, "R", telemetry=True)
    assert job.state is JobState.QUEUED
    assert job.progress == 0.0
    assert not job.is_terminal
    assert job.finished_at is None


def test_progress_increases_through_the_pipeline(registry: FetchRegistry) -> None:
    """A stalled progress bar reads as a hang, so each stage must advance it."""
    job = registry.create(2024, 1, "R", telemetry=True)
    seen = []
    for state in (
        JobState.INGESTING,
        JobState.TRANSFORMING,
        JobState.ANALYSING,
        JobState.COMPLETE,
    ):
        registry.update(job.id, state=state)
        fetched = registry.get(job.id)
        assert fetched is not None
        seen.append(fetched.progress)

    assert seen == sorted(seen), "progress must never move backwards"
    assert seen[-1] == 1.0


def test_terminal_states_stamp_a_finish_time(registry: FetchRegistry) -> None:
    complete = registry.create(2024, 1, "R", telemetry=True)
    failed = registry.create(2024, 2, "R", telemetry=True)

    registry.update(complete.id, state=JobState.COMPLETE)
    registry.update(failed.id, state=JobState.FAILED, error="boom")

    for job_id in (complete.id, failed.id):
        job = registry.get(job_id)
        assert job is not None
        assert job.is_terminal
        assert job.finished_at is not None


def test_finish_time_is_stamped_once(registry: FetchRegistry) -> None:
    """A later update must not restamp the finish time and stretch the duration."""
    job = registry.create(2024, 1, "R", telemetry=True)
    registry.update(job.id, state=JobState.COMPLETE)
    first = registry.get(job.id)
    assert first is not None
    stamped = first.finished_at

    registry.update(job.id, detail="still complete")
    again = registry.get(job.id)
    assert again is not None
    assert again.finished_at == stamped


def test_find_active_matches_only_unfinished_jobs(registry: FetchRegistry) -> None:
    """Deduplication must not block re-fetching a race that already finished."""
    job = registry.create(2024, 5, "R", telemetry=True)
    assert registry.find_active(2024, 5, "R") is not None

    registry.update(job.id, state=JobState.COMPLETE)
    assert registry.find_active(2024, 5, "R") is None


def test_find_active_distinguishes_race_and_kind(registry: FetchRegistry) -> None:
    registry.create(2024, 5, "R", telemetry=True)
    assert registry.find_active(2024, 5, "Q") is None
    assert registry.find_active(2024, 6, "R") is None
    assert registry.find_active(2023, 5, "R") is None


def test_update_of_unknown_job_is_a_no_op(registry: FetchRegistry) -> None:
    """A job may be evicted while its thread is still running."""
    registry.update("does-not-exist", state=JobState.COMPLETE)
    assert registry.get("does-not-exist") is None


def test_eviction_keeps_running_jobs(registry: FetchRegistry) -> None:
    """Bounding history must never drop a job still in flight."""
    running = registry.create(1990, 1, "R", telemetry=False)

    for round_number in range(2, FetchRegistry._MAX_JOBS + 20):
        job = registry.create(2000, round_number, "R", telemetry=False)
        registry.update(job.id, state=JobState.COMPLETE)

    assert registry.get(running.id) is not None
    assert len(registry.all()) <= FetchRegistry._MAX_JOBS


def test_list_returns_newest_first(registry: FetchRegistry) -> None:
    first = registry.create(2024, 1, "R", telemetry=True)
    second = registry.create(2024, 2, "R", telemetry=True)
    ids = [job.id for job in registry.all()]
    assert ids.index(second.id) < ids.index(first.id)


def test_concurrent_updates_do_not_lose_jobs(registry: FetchRegistry) -> None:
    """The lock has to hold under real threads, not just in sequence."""
    jobs = [registry.create(2024, i, "R", telemetry=False) for i in range(1, 21)]

    def finish(job_id: str) -> None:
        registry.update(job_id, state=JobState.COMPLETE, detail="done")

    threads = [threading.Thread(target=finish, args=(j.id,)) for j in jobs]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert all(registry.get(j.id).state is JobState.COMPLETE for j in jobs)  # type: ignore[union-attr]


def test_run_fetch_records_failure_rather_than_raising(monkeypatch) -> None:
    """A thread has nothing to propagate to, so failure must land on the job.

    Without this the thread would die silently and the job would report "ingesting"
    for ever, which looks identical to a slow download.
    """
    from f1x.ingest import jobs as jobs_module

    job = jobs_module.registry.create(2024, 1, "R", telemetry=False)

    class Boom:
        def __init__(self, *_args: object, **_kwargs: object) -> None:
            pass

        def load(self, *_args: object, **_kwargs: object) -> object:
            raise RuntimeError("archive unreachable")

    monkeypatch.setattr("f1x.ingest.FastF1Client", Boom)

    run_fetch(job.id)  # Must not raise.

    finished = jobs_module.registry.get(job.id)
    assert finished is not None
    assert finished.state is JobState.FAILED
    assert finished.error is not None
    assert finished.finished_at is not None


def test_run_fetch_calls_on_finish_even_when_it_fails(monkeypatch) -> None:
    from f1x.ingest import jobs as jobs_module

    job = jobs_module.registry.create(2024, 2, "R", telemetry=False)

    class Boom:
        def __init__(self, *_args: object, **_kwargs: object) -> None:
            pass

        def load(self, *_args: object, **_kwargs: object) -> object:
            raise RuntimeError("nope")

    monkeypatch.setattr("f1x.ingest.FastF1Client", Boom)

    called = threading.Event()
    run_fetch(job.id, on_finish=called.set)
    assert called.is_set()


def test_run_fetch_on_missing_job_is_a_no_op() -> None:
    run_fetch("no-such-job")  # Must not raise.
