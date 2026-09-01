"""FastF1 boundary and ingestion quality gates."""

from __future__ import annotations

import sys
from types import SimpleNamespace

import pandas as pd
import pytest

from f1x.config import ROOT, Settings
from f1x.ingest.exceptions import DataQualityError
from f1x.ingest.fastf1_client import FastF1Client, SessionRequest
from f1x.ingest.loader import _relative_seconds, _session_t0
from f1x.ingest.quality import validate_session


def _source(*, timed: bool = True) -> SimpleNamespace:
    return SimpleNamespace(
        drivers=["44"],
        laps=pd.DataFrame(
            {
                "Driver": ["HAM"],
                "DriverNumber": ["44"],
                "LapNumber": [1],
                "LapTime": [pd.Timedelta(90, unit="s") if timed else pd.NaT],
            }
        ),
        race_control_messages=pd.DataFrame({"Message": ["GREEN LIGHT"]}),
    )


def test_quality_report_counts_a_valid_timed_session() -> None:
    report = validate_session(_source())
    assert report.drivers == 1
    assert report.laps == 1
    assert report.timed_laps == 1
    assert report.messages == 1
    assert report.warnings == ()


def test_quality_gate_rejects_missing_lap_columns() -> None:
    source = _source()
    source.laps = source.laps.drop(columns="LapTime")
    with pytest.raises(DataQualityError, match="LapTime"):
        validate_session(source)


@pytest.mark.parametrize(
    "column, value, message", [("LapNumber", 0, "non-positive"), ("LapNumber", 1, "duplicate")]
)
def test_quality_gate_rejects_invalid_lap_keys(column: str, value: int, message: str) -> None:
    source = _source()
    duplicate = source.laps.copy()
    duplicate.loc[0, column] = value
    source.laps = pd.concat([source.laps, duplicate], ignore_index=True)
    if message == "non-positive":
        source.laps.loc[0, "LapNumber"] = 0
    with pytest.raises(DataQualityError, match=message):
        validate_session(source)


def test_quality_gate_allows_sessions_without_a_timed_lap() -> None:
    report = validate_session(_source(timed=False))
    assert report.timed_laps == 0
    assert report.warnings == ("session has no timed laps",)


def test_session_request_rejects_unknown_session_kind() -> None:
    with pytest.raises(ValueError, match="unsupported session kind"):
        SessionRequest(2024, 1, "TEST")


def test_no_telemetry_load_derives_session_origin_from_laps() -> None:
    source = _source()
    source.laps["LapStartDate"] = [pd.Timestamp("2024-03-02T15:03:00Z")]
    source.laps["LapStartTime"] = [pd.Timedelta(180, unit="s")]
    assert _session_t0(source) == pd.Timestamp("2024-03-02T15:00:00Z").to_pydatetime()


def test_race_control_absolute_timestamp_is_made_session_relative() -> None:
    t0 = pd.Timestamp("2024-03-02T15:00:00Z").to_pydatetime()
    assert _relative_seconds(pd.Timestamp("2024-03-02T15:02:30Z"), t0) == 150


def test_client_enables_cache_and_loads_requested_data(monkeypatch) -> None:
    calls: dict[str, object] = {}

    class FakeCache:
        @staticmethod
        def enable_cache(path: str) -> None:
            calls["cache"] = path

    class FakeSession:
        def load(self, **kwargs: object) -> None:
            calls["load"] = kwargs

    session = FakeSession()

    def get_session(year: int, round_number: int, kind: str) -> FakeSession:
        calls["request"] = (year, round_number, kind)
        return session

    monkeypatch.setitem(
        sys.modules, "fastf1", SimpleNamespace(Cache=FakeCache, get_session=get_session)
    )
    settings = Settings(fastf1_cache_dir=ROOT / ".cache" / "fastf1", debug=True)
    loaded = FastF1Client(settings).load(SessionRequest(2024, 1, "R", telemetry=False))

    assert loaded is session
    assert calls["request"] == (2024, 1, "R")
    assert calls["load"] == {"laps": True, "telemetry": False, "weather": True, "messages": True}
