"""API contract tests.

These run against the live database through FastAPI's TestClient. The point is the
contract rather than the analysis: the numbers are already covered by the engine's own
tests, so what matters here is that responses have the right shape, missing data 404s
with an actionable message, and provenance travels with every analysis result.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from f1x.api.app import create_app
from f1x.config import ENGINE_VERSION

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def client() -> TestClient:
    return TestClient(create_app())


@pytest.fixture(scope="module")
def analysed_session(db_engine) -> int:
    """A session that has been through transform and analyse."""
    with db_engine.connect() as conn:
        session_id = conn.execute(
            text(
                "SELECT session_id FROM mart.pace_rankings "
                "WHERE engine_version = :v LIMIT 1"
            ),
            {"v": ENGINE_VERSION},
        ).scalar_one_or_none()
    if session_id is None:
        pytest.skip("no analysed sessions; run `f1x analyse all` first")
    return int(session_id)


# --------------------------------------------------------------------------
# health and reference
# --------------------------------------------------------------------------


def test_health_reports_the_engine_version(client: TestClient) -> None:
    body = client.get("/health").json()
    assert body["engine_version"] == ENGINE_VERSION
    assert body["database"] == "ok"


def test_seasons_are_listed_newest_first(client: TestClient) -> None:
    seasons = client.get("/api/v1/seasons").json()
    assert seasons
    years = [s["year"] for s in seasons]
    assert years == sorted(years, reverse=True)


def test_events_can_be_filtered_by_season(client: TestClient) -> None:
    seasons = client.get("/api/v1/seasons").json()
    year = seasons[0]["year"]
    events = client.get(f"/api/v1/events?season={year}").json()
    assert events
    assert {e["season_year"] for e in events} == {year}


def test_unknown_session_is_a_404(client: TestClient) -> None:
    assert client.get("/api/v1/sessions/999999").status_code == 404


# --------------------------------------------------------------------------
# analysis
# --------------------------------------------------------------------------


def test_pace_is_ranked_with_gaps_measured_from_the_leader(
    client: TestClient, analysed_session: int
) -> None:
    body = client.get(f"/api/v1/analysis/pace/{analysed_session}").json()
    drivers = body["drivers"]
    assert drivers
    assert [d["rank"] for d in drivers] == list(range(1, len(drivers) + 1))
    assert drivers[0]["gap_to_best_s"] == pytest.approx(0.0, abs=1e-6)
    assert all(d["pace_s"] > 0 for d in drivers)


def test_every_analysis_response_carries_its_engine_version(
    client: TestClient, analysed_session: int
) -> None:
    """Two clients holding different numbers can then tell why they differ."""
    for path in ("analysis/pace", "analysis/degradation"):
        body = client.get(f"/api/v1/{path}/{analysed_session}").json()
        assert body["meta"]["engine_version"] == ENGINE_VERSION


def test_degradation_reports_spread_alongside_the_centre(
    client: TestClient, analysed_session: int
) -> None:
    body = client.get(f"/api/v1/analysis/degradation/{analysed_session}").json()
    assert body["compounds"]
    for compound in body["compounds"]:
        assert compound["degradation_iqr_s"] >= 0
        assert compound["max_stint_laps"] > 0


def test_unphysical_stint_fits_are_returned_not_hidden(
    client: TestClient, analysed_session: int
) -> None:
    """A failed estimate is information about the stint, so it must reach the client."""
    body = client.get(f"/api/v1/analysis/degradation/{analysed_session}").json()
    stints = body["stints"]
    assert stints
    # Whatever the mix, the flag must be present rather than the row filtered away.
    assert all("is_physical" in s for s in stints)
    for stint in stints:
        if stint["degradation_s_per_lap"] < 0:
            assert stint["is_physical"] is False


def test_laps_expose_why_each_was_excluded(
    client: TestClient, analysed_session: int
) -> None:
    laps = client.get(f"/api/v1/analysis/laps/{analysed_session}").json()
    assert laps
    excluded = [x for x in laps if x["is_representative"] is False]
    assert excluded, "a real session always has some excluded laps"
    assert all(x["exclusion_reason"] for x in excluded)


def test_representative_filter_narrows_the_result(
    client: TestClient, analysed_session: int
) -> None:
    all_laps = client.get(f"/api/v1/analysis/laps/{analysed_session}").json()
    clean = client.get(
        f"/api/v1/analysis/laps/{analysed_session}?representative_only=true"
    ).json()
    assert 0 < len(clean) < len(all_laps)
    assert all(x["is_representative"] for x in clean)


def test_missing_analysis_404s_with_an_actionable_message(client: TestClient) -> None:
    """The error should say what to run, not just that nothing was found."""
    response = client.get("/api/v1/analysis/pace/999999")
    assert response.status_code == 404
    assert "analyse" in response.json()["detail"]


# --------------------------------------------------------------------------
# strategy, simulation, ratings
# --------------------------------------------------------------------------


def test_strategy_options_are_ranked_cheapest_first(
    client: TestClient, analysed_session: int
) -> None:
    response = client.get(f"/api/v1/strategy/{analysed_session}")
    if response.status_code == 404:
        pytest.skip("session has too few clean pit stops")
    body = response.json()
    costs = [o["total_cost_s"] for o in body["options"]]
    assert costs == sorted(costs)
    assert body["pit_loss"]["net_loss_s"] > 0


def test_a_dry_race_strategy_always_includes_a_stop(
    client: TestClient, analysed_session: int
) -> None:
    """Two compounds are mandatory, so zero stops is not a legal option."""
    response = client.get(f"/api/v1/strategy/{analysed_session}")
    if response.status_code == 404:
        pytest.skip("session has too few clean pit stops")
    assert min(o["n_stops"] for o in response.json()["options"]) >= 1


def test_simulation_win_rates_sum_to_one(
    client: TestClient, analysed_session: int
) -> None:
    response = client.get(f"/api/v1/simulate/{analysed_session}?iterations=200")
    if response.status_code == 404:
        pytest.skip("session cannot be simulated")
    body = response.json()
    total = sum(s["win_rate"] for s in body["strategies"])
    assert total == pytest.approx(1.0, abs=0.01)


def test_simulation_rejects_an_absurd_iteration_count(client: TestClient) -> None:
    assert client.get("/api/v1/simulate/1?iterations=5").status_code == 422


def test_ratings_carry_the_relative_scale_caveat(client: TestClient) -> None:
    seasons = client.get("/api/v1/seasons").json()
    response = client.get(f"/api/v1/ratings/{seasons[0]['year']}")
    if response.status_code == 404:
        pytest.skip("season not analysed")
    body = response.json()
    assert "relative" in body["note"]
    drivers = body["drivers"]
    assert [d["rank"] for d in drivers] == list(range(1, len(drivers) + 1))
    assert all(0 <= d["overall"] <= 100 for d in drivers)


# --------------------------------------------------------------------------
# openapi
# --------------------------------------------------------------------------


def test_openapi_schema_generates(client: TestClient) -> None:
    """The frontend's typed client is generated from this, so it must be valid."""
    schema = client.get("/openapi.json").json()
    assert schema["info"]["version"] == ENGINE_VERSION
    assert len(schema["paths"]) >= 10


def test_estimated_fields_document_that_they_are_estimates(client: TestClient) -> None:
    """A number that travels without its provenance gets treated as fact."""
    schema = client.get("/openapi.json").json()
    lap = schema["components"]["schemas"]["LapOut"]["properties"]
    assert "estimate" in lap["fuel_corrected_s"]["description"].lower()
