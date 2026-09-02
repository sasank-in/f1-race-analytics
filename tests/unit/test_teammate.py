"""Teammate comparison.

The distinction these tests protect is between *on average ahead* and *consistently
ahead*. A small mean built from wild swings says much less than the same mean built
from one driver winning most weekends, and the head-to-head count is what separates
them.
"""

from __future__ import annotations

import polars as pl
import pytest

from f1x.engine.pace.teammate import MIN_SHARED_SESSIONS, compare_teammates


def _pace(rows: list[tuple[int, str, float]]) -> pl.DataFrame:
    return pl.DataFrame(
        [{"session_id": s, "driver_number": d, "pace_s": p} for s, d, p in rows]
    )


def _entries(rows: list[tuple[int, str, str]]) -> pl.DataFrame:
    return pl.DataFrame(
        [{"session_id": s, "driver_number": d, "team_key": t} for s, d, t in rows]
    )


def _pairing(gaps: list[float], team: str = "alpha") -> list:
    """A pairing where driver 1 is quicker than driver 2 by each listed gap."""
    pace, entries = [], []
    for i, gap in enumerate(gaps, start=1):
        pace += [(i, "1", 90.0), (i, "2", 90.0 + gap)]
        entries += [(i, "1", team), (i, "2", team)]
    return compare_teammates(_pace(pace), _entries(entries))


def test_the_quicker_driver_is_identified() -> None:
    result = _pairing([0.2, 0.3, 0.25, 0.15, 0.2])
    assert len(result) == 1
    assert result[0].faster_driver == "1"
    assert result[0].margin_s == pytest.approx(0.2, abs=0.05)


def test_head_to_head_counts_both_directions() -> None:
    result = _pairing([0.2, 0.3, -0.1, 0.25, -0.2])[0]
    assert result.sessions_a_ahead == 3
    assert result.sessions_b_ahead == 2
    assert result.n_sessions == 5


def test_a_consistent_winner_is_decisive() -> None:
    """Ahead in most weekends, not merely ahead on average."""
    result = _pairing([0.2, 0.25, 0.3, 0.15, 0.2, 0.22, 0.28])[0]
    assert result.is_decisive is True


def test_an_even_pairing_is_not_decisive() -> None:
    """Leclerc and Sainz at 10-9 is a real result: they were inseparable."""
    result = _pairing([0.3, -0.3, 0.2, -0.25, 0.1, -0.15])[0]
    assert result.is_decisive is False


def test_spread_distinguishes_a_settled_result_from_a_noisy_one() -> None:
    steady = _pairing([0.2, 0.2, 0.21, 0.19, 0.2])[0]
    noisy = _pairing([1.2, -0.8, 0.9, -0.7, 0.4])[0]
    assert steady.std_delta_s < noisy.std_delta_s


def test_a_thin_pairing_is_flagged_unreliable() -> None:
    """Two races is not a season."""
    result = _pairing([0.2] * (MIN_SHARED_SESSIONS - 1))[0]
    assert result.is_reliable is False


def test_an_implausible_gap_is_flagged_unreliable() -> None:
    """A five-second delta is a broken car, not a driver difference."""
    result = _pairing([5.0, 5.2, 4.8, 5.1])[0]
    assert result.is_reliable is False


def test_only_shared_sessions_are_compared() -> None:
    """A race one driver did not finish says nothing about who is quicker."""
    pace = _pace([(1, "1", 90.0), (1, "2", 90.3), (2, "1", 90.0)])
    entries = _entries([(1, "1", "a"), (1, "2", "a"), (2, "1", "a"), (2, "2", "a")])
    result = compare_teammates(pace, entries)[0]
    assert result.n_sessions == 1


def test_drivers_on_different_teams_are_not_compared() -> None:
    """The whole premise is shared machinery."""
    pace = _pace([(1, "1", 90.0), (1, "2", 90.3)])
    entries = _entries([(1, "1", "alpha"), (1, "2", "beta")])
    assert compare_teammates(pace, entries) == []


def test_a_mid_season_change_produces_separate_pairings() -> None:
    """Averaging across a driver swap would describe a car that never existed."""
    pace = _pace(
        [
            (1, "1", 90.0), (1, "2", 90.3),
            (2, "1", 90.0), (2, "2", 90.3),
            (3, "1", 90.0), (3, "3", 90.5),
            (4, "1", 90.0), (4, "3", 90.5),
        ]
    )
    entries = _entries(
        [
            (1, "1", "a"), (1, "2", "a"),
            (2, "1", "a"), (2, "2", "a"),
            (3, "1", "a"), (3, "3", "a"),
            (4, "1", "a"), (4, "3", "a"),
        ]
    )
    result = compare_teammates(pace, entries)
    pairs = {(r.driver_a, r.driver_b) for r in result}
    assert ("1", "2") in pairs
    assert ("1", "3") in pairs


def test_empty_input_produces_nothing() -> None:
    assert compare_teammates(pl.DataFrame(), pl.DataFrame()) == []
