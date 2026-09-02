"""Transform rules, checked on hand-built frames.

Every function under test is pure, so these run without a database and assert the
analytical contract directly: which laps count, what a correction does to them, and
what the derivations reconstruct.
"""

from __future__ import annotations

import polars as pl
import pytest

from f1x.transform import corrections, stints, track_status, validity
from f1x.transform.pipeline import transform_session

# --------------------------------------------------------------------------
# track status
# --------------------------------------------------------------------------


def test_concatenated_codes_decode_to_a_set() -> None:
    """'2671' is four codes, not a number. Observed verbatim in 2023 Bahrain."""
    assert track_status.decode("2671") == {"2", "6", "7", "1"}


def test_plain_green_lap_is_green() -> None:
    assert track_status.is_green("1") is True


def test_multi_status_lap_is_not_green() -> None:
    # Contains a yellow (2) and a VSC (6): not a clean racing lap.
    assert track_status.is_green("2671") is False


def test_missing_status_is_not_treated_as_green() -> None:
    """Absence of evidence is not evidence the lap was clean."""
    assert track_status.is_green(None) is False
    assert track_status.is_green("") is False


@pytest.mark.parametrize(("raw", "expected"), [("4", True), ("6", True), ("5", True), ("1", False)])
def test_neutralisation_detection(raw: str, expected: bool) -> None:
    assert track_status.is_neutralised(raw) is expected


def test_unknown_code_is_preserved_not_dropped() -> None:
    """A future status code must surface in the data rather than vanish silently."""
    assert "9" in track_status.decode("19")
    assert "unknown (9)" in track_status.describe("19")


# --------------------------------------------------------------------------
# validity
# --------------------------------------------------------------------------


def _laps(**overrides: object) -> pl.DataFrame:
    base = {
        "session_id": [1] * 4,
        "driver_number": ["44"] * 4,
        "lap_number": [1, 2, 3, 4],
        "lap_time_s": [95.0, 90.0, 91.0, 92.0],
        "track_status": ["1", "1", "1", "1"],
        "deleted": [False] * 4,
        "is_accurate": [True] * 4,
        "pit_in_s": [None, None, None, None],
        "pit_out_s": [None, None, None, None],
        "position": [1.0] * 4,
        "stint": [1] * 4,
        "compound": ["SOFT"] * 4,
        "tyre_life": [1.0, 2.0, 3.0, 4.0],
        "fresh_tyre": [True] * 4,
    }
    base.update(overrides)
    return pl.DataFrame(base)


def test_clean_laps_are_representative() -> None:
    out = validity.classify(_laps())
    assert out.get_column("is_representative").sum() == 4


def test_deleted_lap_is_excluded_with_its_reason() -> None:
    out = validity.classify(_laps(deleted=[True, False, False, False]))
    reasons = out.get_column("exclusion_reason").to_list()
    assert reasons[0] == validity.Exclusion.DELETED.value
    assert out.get_column("is_representative").to_list()[0] is False


def test_in_and_out_laps_are_excluded() -> None:
    out = validity.classify(
        _laps(pit_in_s=[None, 20.0, None, None], pit_out_s=[None, None, 25.0, None])
    )
    reasons = out.get_column("exclusion_reason").to_list()
    assert reasons[1] == validity.Exclusion.IN_LAP.value
    assert reasons[2] == validity.Exclusion.OUT_LAP.value


def test_neutralised_lap_is_excluded() -> None:
    out = validity.classify(_laps(track_status=["1", "4", "1", "1"]))
    assert out.get_column("exclusion_reason").to_list()[1] == validity.Exclusion.NEUTRALISED.value


def test_slow_lap_is_flagged_as_an_outlier() -> None:
    """A lap far off the session best did not represent competitive pace."""
    out = validity.classify(_laps(lap_time_s=[90.0, 91.0, 92.0, 140.0]))
    assert out.get_column("exclusion_reason").to_list()[3] == validity.Exclusion.OUTLIER.value


def test_most_fundamental_exclusion_wins() -> None:
    """A deleted lap that is also an in-lap is reported as deleted, not as an in-lap."""
    out = validity.classify(
        _laps(deleted=[True, False, False, False], pit_in_s=[20.0, None, None, None])
    )
    assert out.get_column("exclusion_reason").to_list()[0] == validity.Exclusion.DELETED.value


def test_empty_frame_classifies_without_error() -> None:
    assert validity.classify(pl.DataFrame()).is_empty()


# --------------------------------------------------------------------------
# corrections
# --------------------------------------------------------------------------


def test_fuel_correction_removes_the_burn_off_trend() -> None:
    """The point of the correction: a constant-effort stint should look flat."""
    # Times improving by exactly the fuel effect each lap (0.03 s/kg * 2 kg/lap).
    laps = pl.DataFrame(
        {
            "session_id": [1] * 5,
            "driver_number": ["44"] * 5,
            "lap_number": [1, 2, 3, 4, 5],
            "lap_time_s": [93.0, 92.94, 92.88, 92.82, 92.76],
        }
    )
    out = corrections.add_fuel_correction(laps, total_laps=50)
    corrected = out.get_column("fuel_corrected_s").to_list()
    assert max(corrected) - min(corrected) < 0.01, "corrected stint should be flat"


def test_fuel_load_decreases_over_the_race() -> None:
    laps = pl.DataFrame(
        {
            "session_id": [1, 1],
            "driver_number": ["44", "44"],
            "lap_number": [1, 50],
            "lap_time_s": [95.0, 92.0],
        }
    )
    fuel = corrections.add_fuel_correction(laps, total_laps=50).get_column("fuel_load_kg")
    assert fuel[0] == pytest.approx(100.0)
    assert fuel[1] < fuel[0]


def test_no_fuel_correction_without_a_race_distance() -> None:
    """Practice and qualifying carry unknown fuel loads; inventing one is worse than null."""
    laps = pl.DataFrame(
        {
            "session_id": [1],
            "driver_number": ["44"],
            "lap_number": [1],
            "lap_time_s": [90.0],
        }
    )
    out = corrections.add_fuel_correction(laps, total_laps=None)
    assert out.get_column("fuel_corrected_s").to_list() == [None]


def test_leader_is_always_in_clean_air() -> None:
    laps = pl.DataFrame(
        {
            "session_id": [1, 1],
            "driver_number": ["44", "1"],
            "lap_number": [1, 1],
            "lap_time_s": [90.0, 90.5],
            "position": [1.0, 2.0],
        }
    )
    out = corrections.add_traffic_state(laps)
    leader = out.filter(pl.col("position") == 1).get_column("is_clean_air").to_list()
    assert leader == [True]


def test_car_close_behind_is_not_in_clean_air() -> None:
    laps = pl.DataFrame(
        {
            "session_id": [1, 1],
            "driver_number": ["44", "1"],
            "lap_number": [1, 1],
            "lap_time_s": [90.0, 90.4],  # 0.4s behind: inside the dirty-air threshold
            "position": [1.0, 2.0],
        }
    )
    out = corrections.add_traffic_state(laps)
    follower = out.filter(pl.col("position") == 2).get_column("is_clean_air").to_list()
    assert follower == [False]


# --------------------------------------------------------------------------
# stints and pit stops
# --------------------------------------------------------------------------


def test_stints_collapse_to_one_row_each() -> None:
    laps = _laps(stint=[1, 1, 2, 2], compound=["SOFT", "SOFT", "HARD", "HARD"])
    out = stints.derive_stints(laps)
    assert len(out) == 2
    first = out.filter(pl.col("stint") == 1).to_dicts()[0]
    assert first["compound"] == "SOFT"
    assert first["start_lap"] == 1
    assert first["end_lap"] == 2
    assert first["n_laps"] == 2


def test_stint_records_the_age_of_the_tyre_it_started_on() -> None:
    """A used set carries degradation the lap count alone would not reveal."""
    laps = _laps(stint=[1, 1, 2, 2], tyre_life=[1.0, 2.0, 7.0, 8.0])
    out = stints.derive_stints(laps)
    second = out.filter(pl.col("stint") == 2).to_dicts()[0]
    assert second["tyre_age_start"] == 7.0


def test_pit_stop_pairs_in_lap_entry_with_out_lap_exit() -> None:
    """Duration spans pit entry to pit exit, so it includes pit-lane travel."""
    laps = _laps(
        pit_in_s=[None, 100.0, None, None],
        pit_out_s=[None, None, 125.0, None],
    )
    out = stints.derive_pit_stops(laps)
    assert len(out) == 1
    stop = out.to_dicts()[0]
    assert stop["lap_number"] == 2
    assert stop["pit_duration_s"] == pytest.approx(25.0)


def test_pit_stop_without_an_out_lap_has_no_duration() -> None:
    """A car that retires in the pits never exits; the duration is unknown, not zero."""
    laps = _laps(pit_in_s=[None, None, None, 100.0])
    out = stints.derive_pit_stops(laps)
    assert out.to_dicts()[0]["pit_duration_s"] is None


def test_no_pit_stops_yields_an_empty_frame_not_an_error() -> None:
    assert stints.derive_pit_stops(_laps()).is_empty()


# --------------------------------------------------------------------------
# pipeline
# --------------------------------------------------------------------------


def test_pipeline_produces_the_mart_columns() -> None:
    from f1x.transform.pipeline import METRIC_COLUMNS

    result = transform_session(_laps(), total_laps=50)
    assert tuple(result.lap_metrics.columns) == METRIC_COLUMNS


def test_pipeline_reports_why_laps_were_excluded() -> None:
    result = transform_session(_laps(deleted=[True, False, False, False]), total_laps=50)
    assert result.exclusions[validity.Exclusion.DELETED.value] == 1
    assert result.representative_laps == 3


def test_pipeline_stamps_the_engine_version() -> None:
    """Derived rows must carry the version that produced them, or caches go stale silently."""
    from f1x.config import ENGINE_VERSION

    assert transform_session(_laps(), total_laps=50).engine_version == ENGINE_VERSION


def test_empty_frame_stays_empty_through_every_transform() -> None:
    """A zero-row frame must not gain a phantom all-null lap from with_columns."""
    empty = pl.DataFrame()
    assert validity.classify(empty).is_empty()
    assert corrections.add_fuel_correction(empty, total_laps=50).is_empty()
    assert corrections.add_traffic_state(empty).is_empty()
    assert stints.derive_stints(empty).is_empty()
    assert stints.derive_pit_stops(empty).is_empty()


def test_evolution_correction_removes_a_session_wide_grip_trend() -> None:
    """Track evolution is a field-wide effect, not a property of one driver's tyres."""
    rows = []
    for driver in range(6):
        for lap in range(1, 31):
            rows.append(
                {
                    "session_id": 1,
                    "driver_number": str(driver),
                    "lap_number": lap,
                    "lap_time_s": 95.0,
                    # Every car improves by the same amount as the track rubbers in.
                    "fuel_corrected_s": 95.0 - 0.02 * (lap - 1) + driver * 0.1,
                    "is_representative": True,
                }
            )
    out = corrections.add_evolution_correction(pl.DataFrame(rows))
    corrected = out.get_column("evolution_corrected_s").to_numpy()
    lap_numbers = out.get_column("lap_number").to_numpy()
    trend = float(pl.DataFrame({"a": lap_numbers, "b": corrected}).select(
        pl.corr("a", "b")
    ).item())
    assert abs(trend) < 0.2, "the grip trend should be gone after correcting"


def test_evolution_correction_ignores_a_worsening_track() -> None:
    """A field getting slower is degradation, not evolution, and belongs to the tyre."""
    rows = [
        {
            "session_id": 1,
            "driver_number": "1",
            "lap_number": lap,
            "lap_time_s": 95.0,
            "fuel_corrected_s": 95.0 + 0.05 * lap,
            "is_representative": True,
        }
        for lap in range(1, 61)
    ]
    assert corrections.estimate_evolution(pl.DataFrame(rows)) == 0.0


def test_fuel_coefficient_is_declared_as_a_published_default() -> None:
    """Provenance is part of the contract.

    Three attempts to fit this from race timing failed because fuel_load_kg is
    constructed from lap number. Marking it fitted would misrepresent the method.
    """
    assert corrections.FUEL_EFFECT_SOURCE == "published_default"
    assert corrections.FUEL_EFFECT_FITTED is False
    assert 0.025 <= corrections.FUEL_EFFECT_S_PER_KG <= 0.040
