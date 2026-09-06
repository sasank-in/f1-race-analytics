"""Fetch router contract.

The schedule and fetch endpoints reach the network, so every test here stubs that
boundary. What is under test is the router's own behaviour: validation, deduplication
and the shape of what it returns.
"""

from __future__ import annotations

import datetime as dt

import pytest
from fastapi.testclient import TestClient

from f1x.ingest.jobs import JobState
from f1x.ingest.schedule import ScheduledRace


@pytest.fixture
def client(monkeypatch) -> TestClient:
    """An app whose fetch router never touches the archive or the database."""
    from f1x.api.app import create_app

    def fake_schedule(season: int, settings=None) -> list[ScheduledRace]:
        return [
            ScheduledRace(
                season=season,
                round_number=1,
                name="Bahrain Grand Prix",
                country="Bahrain",
                event_date=dt.date(season, 3, 2),
                has_run=True,
                is_ingested=False,
            ),
            ScheduledRace(
                season=season,
                round_number=2,
                name="Future Grand Prix",
                country="Nowhere",
                event_date=dt.date(season + 5, 12, 1),
                has_run=False,
                is_ingested=False,
            ),
        ]

    monkeypatch.setattr("f1x.api.routers.fetch.fetch_schedule", fake_schedule)
    # Leave local status alone: annotating needs a database this test does not have.
    monkeypatch.setattr(
        "f1x.api.routers.fetch.annotate_ingested", lambda races, _engine: races
    )
    # The app's lifespan pings the database at startup.
    monkeypatch.setattr("f1x.api.app.get_engine", lambda: _StubEngine())
    monkeypatch.setattr("f1x.api.routers.fetch.get_engine", lambda: _StubEngine())

    with TestClient(create_app()) as test_client:
        yield test_client


class _StubConnection:
    def execute(self, *_args: object, **_kwargs: object) -> object:
        return _StubResult()

    def __enter__(self) -> _StubConnection:
        return self

    def __exit__(self, *args: object) -> None:
        return None


class _StubResult:
    def all(self) -> list[tuple[int, int]]:
        return []

    def scalar(self) -> int:
        return 0


class _StubEngine:
    def connect(self) -> _StubConnection:
        return _StubConnection()

    def dispose(self) -> None:
        return None


def test_schedule_lists_the_published_calendar(client: TestClient) -> None:
    response = client.get("/api/v1/schedule/2024")
    assert response.status_code == 200

    body = response.json()
    assert body["season"] == 2024
    assert len(body["races"]) == 2
    assert body["races"][0]["name"] == "Bahrain Grand Prix"


def test_schedule_marks_races_that_have_not_run(client: TestClient) -> None:
    """A future race must be distinguishable, or the UI offers a fetch that cannot work."""
    races = client.get("/api/v1/schedule/2024").json()["races"]
    assert races[0]["has_run"] is True
    assert races[1]["has_run"] is False


def test_schedule_rejects_a_season_without_lap_timing(client: TestClient) -> None:
    """Before 2018 a session loads with no laps, so there is nothing to analyse."""
    response = client.get("/api/v1/schedule/2005")
    assert response.status_code == 400
    assert "lap timing" in response.json()["detail"]


def test_fetch_rejects_an_unknown_session_kind(client: TestClient) -> None:
    response = client.post(
        "/api/v1/fetch", json={"year": 2024, "round": 1, "kind": "BRUNCH"}
    )
    assert response.status_code == 400
    assert "kind" in response.json()["detail"]


@pytest.mark.parametrize("telemetry", [True, False])
def test_fetch_rejects_a_season_without_lap_timing(
    client: TestClient, telemetry: bool
) -> None:
    """Refused regardless of the telemetry flag.

    Disabling telemetry does not make a 2005 race analysable — it has no laps either
    way, so allowing it would store a session that every page renders empty.
    """
    response = client.post(
        "/api/v1/fetch",
        json={"year": 2005, "round": 1, "kind": "R", "telemetry": telemetry},
    )
    assert response.status_code == 400
    assert "lap timing" in response.json()["detail"]


def test_fetch_starts_a_job_and_returns_202(client: TestClient, monkeypatch) -> None:
    from f1x.ingest import jobs as jobs_module

    started: list[str] = []

    def fake_start(year: int, round_number: int, kind: str, telemetry: bool):
        job = jobs_module.registry.create(year, round_number, kind, telemetry)
        started.append(job.id)
        return job

    monkeypatch.setattr("f1x.api.routers.fetch.start_fetch", fake_start)

    response = client.post("/api/v1/fetch", json={"year": 2024, "round": 3, "kind": "R"})
    assert response.status_code == 202

    body = response.json()
    assert body["state"] == JobState.QUEUED.value
    assert body["round"] == 3
    assert body["progress"] == 0.0
    assert body["id"] in started


def test_fetch_deduplicates_a_race_already_running(client: TestClient, monkeypatch) -> None:
    """A double-click must not start two threads writing the same rows."""
    from f1x.ingest import jobs as jobs_module

    calls: list[int] = []

    def fake_start(year: int, round_number: int, kind: str, telemetry: bool):
        calls.append(1)
        return jobs_module.registry.create(year, round_number, kind, telemetry)

    monkeypatch.setattr("f1x.api.routers.fetch.start_fetch", fake_start)

    payload = {"year": 2024, "round": 9, "kind": "R"}
    first = client.post("/api/v1/fetch", json=payload).json()
    second = client.post("/api/v1/fetch", json=payload).json()

    assert first["id"] == second["id"]
    assert len(calls) == 1, "the second request must reuse the running job"


def test_polling_an_unknown_job_is_404(client: TestClient) -> None:
    response = client.get("/api/v1/fetch/nope")
    assert response.status_code == 404


def test_job_can_be_polled_after_starting(client: TestClient, monkeypatch) -> None:
    from f1x.ingest import jobs as jobs_module

    monkeypatch.setattr(
        "f1x.api.routers.fetch.start_fetch",
        lambda y, r, k, t: jobs_module.registry.create(y, r, k, t),
    )
    job_id = client.post(
        "/api/v1/fetch", json={"year": 2024, "round": 11, "kind": "R"}
    ).json()["id"]

    body = client.get(f"/api/v1/fetch/{job_id}").json()
    assert body["id"] == job_id
    assert body["year"] == 2024
