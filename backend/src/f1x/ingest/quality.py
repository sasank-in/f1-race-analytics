"""Quality gates applied before a FastF1 session is written to the warehouse."""

# ruff: noqa: ANN401

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pandas as pd  # type: ignore[import-untyped]

from f1x.ingest.exceptions import DataQualityError


@dataclass(frozen=True)
class QualityReport:
    """Small, serialisable summary stored alongside every ingest run."""

    drivers: int
    laps: int
    timed_laps: int
    messages: int
    warnings: tuple[str, ...] = ()


def require_columns(frame: pd.DataFrame, columns: set[str], *, label: str) -> None:
    """Reject an incomplete source frame before any database writes occur."""
    missing = sorted(columns.difference(frame.columns))
    if missing:
        raise DataQualityError(f"{label} is missing required columns: {', '.join(missing)}")


def validate_session(source: Any) -> QualityReport:
    """Validate the minimum contract needed to materialise a session.

    A session can legitimately have no lap timing (for example a cancelled practice),
    so that case is reported as a warning rather than treated as corrupt input.
    """
    drivers = list(getattr(source, "drivers", ()))
    if not drivers:
        raise DataQualityError("session contains no drivers")

    laps = getattr(source, "laps", None)
    if not isinstance(laps, pd.DataFrame):
        raise DataQualityError("session has no laps dataframe")
    require_columns(laps, {"Driver", "DriverNumber", "LapNumber", "LapTime"}, label="laps")
    invalid_lap_numbers = laps["LapNumber"].dropna().le(0)
    if invalid_lap_numbers.any():
        raise DataQualityError("laps contains a non-positive lap number")
    duplicate_laps = laps.duplicated(["DriverNumber", "LapNumber"], keep=False)
    if duplicate_laps.any():
        raise DataQualityError("laps contains duplicate driver/lap-number pairs")

    timed_laps = int(laps["LapTime"].notna().sum())
    warnings: list[str] = []
    if laps.empty:
        warnings.append("session has no recorded laps")
    elif timed_laps == 0:
        warnings.append("session has no timed laps")

    messages = getattr(source, "race_control_messages", pd.DataFrame())
    if not isinstance(messages, pd.DataFrame):
        warnings.append("race-control messages unavailable")
        message_count = 0
    else:
        message_count = len(messages)

    return QualityReport(
        drivers=len(drivers),
        laps=len(laps),
        timed_laps=timed_laps,
        messages=message_count,
        warnings=tuple(warnings),
    )
