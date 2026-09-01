"""Decoding of FastF1's track-status field.

The feed reports track status as a string of concatenated single-digit codes covering
everything that applied during a lap. A lap run partly under yellow and partly behind
the safety car arrives as ``'2671'`` — four codes, not the number two thousand six
hundred and seventy one. Comparing the field as a value is therefore always wrong;
it has to be decomposed into the set of conditions it represents.

Observed in real data (2023 Bahrain): '1', '12', '21', '126', '671', '2671'.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Final


class TrackStatus(StrEnum):
    """Single-digit codes used by the F1 timing feed."""

    ALL_CLEAR = "1"
    YELLOW = "2"
    GREEN = "3"          # shown when the track goes green again after a stoppage
    SC_DEPLOYED = "4"
    RED = "5"
    VSC_DEPLOYED = "6"
    VSC_ENDING = "7"


# Conditions under which a lap time does not represent competitive pace.
NEUTRALISED: Final[frozenset[str]] = frozenset(
    {
        TrackStatus.SC_DEPLOYED,
        TrackStatus.RED,
        TrackStatus.VSC_DEPLOYED,
        TrackStatus.VSC_ENDING,
    }
)

# Yellow flags slow a driver but only in one sector, so they are treated separately
# from a full neutralisation: a yellow lap is suspect, an SC lap is meaningless.
CAUTION: Final[frozenset[str]] = frozenset({TrackStatus.YELLOW})


def decode(raw: str | None) -> frozenset[str]:
    """Split a raw status string into the set of codes that applied during the lap.

    Unknown digits are preserved rather than dropped, so a future code shows up in
    the data instead of silently vanishing.
    """
    if not raw:
        return frozenset()
    return frozenset(ch for ch in str(raw).strip() if ch.isdigit())


def is_green(raw: str | None) -> bool:
    """True when the lap ran wholly under racing conditions.

    A missing status is treated as not-green: absence of evidence is not evidence
    that the lap was clean, and a pace sample should never include a lap we cannot
    vouch for.
    """
    codes = decode(raw)
    if not codes:
        return False
    return not (codes & (NEUTRALISED | CAUTION))


def is_neutralised(raw: str | None) -> bool:
    """True when a safety car, virtual safety car or red flag applied."""
    return bool(decode(raw) & NEUTRALISED)


def has_yellow(raw: str | None) -> bool:
    """True when any yellow flag applied during the lap."""
    return bool(decode(raw) & CAUTION)


def describe(raw: str | None) -> tuple[str, ...]:
    """Human-readable condition names, for diagnostics and API responses."""
    names: dict[str, str] = {
        TrackStatus.ALL_CLEAR: "all clear",
        TrackStatus.YELLOW: "yellow",
        TrackStatus.GREEN: "green",
        TrackStatus.SC_DEPLOYED: "safety car",
        TrackStatus.RED: "red flag",
        TrackStatus.VSC_DEPLOYED: "virtual safety car",
        TrackStatus.VSC_ENDING: "virtual safety car ending",
    }
    return tuple(names.get(code, f"unknown ({code})") for code in sorted(decode(raw)))
