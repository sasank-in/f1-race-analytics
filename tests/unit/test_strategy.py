"""Strategy models, checked on constructed cases with known answers."""

from __future__ import annotations

import polars as pl
import pytest

from f1x.engine.strategy import optimiser, pit_loss, undercut

# --------------------------------------------------------------------------
# pit loss
# --------------------------------------------------------------------------


def _stops_and_laps(
    n_drivers: int = 6, *, reference: float = 95.0, excess: float = 22.0
) -> tuple[pl.DataFrame, pl.DataFrame]:
    """A session where every driver pits once, losing a known amount of time."""
    stops, laps = [], []
    for driver in range(n_drivers):
        number = str(40 + driver)
        stops.append(
            {
                "session_id": 1,
                "driver_number": number,
                "stop_number": 1,
                "lap_number": 20,
                "pit_duration_s": 24.0,
            }
        )
        for lap in range(1, 41):
            # The in-lap and out-lap together carry the excess.
            if lap == 20:
                time = reference + excess * 0.3
            elif lap == 21:
                time = reference + excess * 0.7
            else:
                time = reference
            laps.append(
                {
                    "session_id": 1,
                    "driver_number": number,
                    "lap_number": lap,
                    "lap_time_s": time,
                    "is_representative": lap not in (20, 21),
                }
            )
    return pl.DataFrame(stops), pl.DataFrame(laps)


def test_pit_loss_recovers_a_known_excess() -> None:
    """The cost of a stop is what the in-lap and out-lap add beyond two normal laps."""
    stops, laps = _stops_and_laps(excess=22.0)
    result = pit_loss.estimate_from_laps(stops, laps, session_id=1)
    assert result is not None
    assert result.net_loss_s == pytest.approx(22.0, abs=0.5)


def test_pit_loss_is_not_the_pit_lane_transit() -> None:
    """pit_duration_s measures transit only; the real cost is larger."""
    stops, laps = _stops_and_laps(excess=22.0)
    result = pit_loss.estimate_from_laps(stops, laps, session_id=1)
    assert result is not None
    assert result.net_loss_s > result.pit_window_s * 0.5
    assert result.on_track_equivalent_s == pytest.approx(95.0, abs=0.5)


def test_too_few_stops_gives_no_estimate() -> None:
    stops, laps = _stops_and_laps(n_drivers=2)
    assert pit_loss.estimate_from_laps(stops, laps, session_id=1) is None


def test_implausible_stop_is_excluded() -> None:
    """A stop under a safety car or with a repair does not describe a clean stop."""
    stops, laps = _stops_and_laps(excess=200.0)
    assert pit_loss.estimate_from_laps(stops, laps, session_id=1) is None


def test_empty_input_gives_no_estimate() -> None:
    assert pit_loss.estimate_from_laps(pl.DataFrame(), pl.DataFrame(), session_id=1) is None


# --------------------------------------------------------------------------
# optimiser
# --------------------------------------------------------------------------


def test_degradation_cost_is_quadratic_in_stint_length() -> None:
    """Doubling a stint more than doubles its degradation cost — the reason to split."""
    short = optimiser.degradation_cost(10, 0.1)
    long = optimiser.degradation_cost(20, 0.1)
    assert long > 2 * short


def test_stints_split_as_evenly_as_the_lap_count_allows() -> None:
    assert optimiser.split_evenly(50, 2) == (25, 25)
    assert optimiser.split_evenly(51, 2) == (26, 25)
    assert sum(optimiser.split_evenly(57, 3)) == 57


def test_high_degradation_justifies_more_stops() -> None:
    low = optimiser.optimal_stop_count(
        total_laps=50, slope_s_per_lap=0.03, net_pit_loss_s=22.0
    )
    high = optimiser.optimal_stop_count(
        total_laps=50, slope_s_per_lap=0.30, net_pit_loss_s=22.0
    )
    assert high > low


def test_expensive_pit_lane_discourages_stopping() -> None:
    cheap = optimiser.optimal_stop_count(
        total_laps=50, slope_s_per_lap=0.15, net_pit_loss_s=15.0
    )
    dear = optimiser.optimal_stop_count(
        total_laps=50, slope_s_per_lap=0.15, net_pit_loss_s=40.0
    )
    assert dear <= cheap


def test_dry_race_always_requires_at_least_one_stop() -> None:
    """Two compounds are mandatory, so zero stops is illegal however low degradation is."""
    options = optimiser.optimise(
        total_laps=50, slope_s_per_lap=0.001, net_pit_loss_s=25.0
    )
    assert min(option.n_stops for option in options) == 1


def test_wet_race_may_run_without_a_stop() -> None:
    """The two-compound rule does not apply once a wet tyre has been used."""
    options = optimiser.optimise(
        total_laps=50, slope_s_per_lap=0.001, net_pit_loss_s=25.0, min_stops=0
    )
    assert options[0].n_stops == 0


def test_options_are_ranked_cheapest_first() -> None:
    options = optimiser.optimise(
        total_laps=57, slope_s_per_lap=0.12, net_pit_loss_s=21.0
    )
    costs = [option.total_cost_s for option in options]
    assert costs == sorted(costs)


def test_zero_distance_race_has_no_strategy() -> None:
    assert optimiser.optimise(total_laps=0, slope_s_per_lap=0.1, net_pit_loss_s=20.0) == []


# --------------------------------------------------------------------------
# undercut
# --------------------------------------------------------------------------


def _window(**kw: float) -> undercut.UndercutWindow:
    params = {
        "gap_s": 1.0,
        "defender_tyre_age": 20.0,
        "degradation_s_per_lap": 0.1,
        "net_pit_loss_s": 22.0,
    }
    params.update(kw)
    return undercut.evaluate_undercut(
        session_id=1,
        attacker="44",
        defender="1",
        lap_number=20,
        **params,  # type: ignore[arg-type]
    )


def test_undercut_works_when_the_gain_exceeds_the_gap() -> None:
    """Worn tyres ahead and a small gap: the classic undercut."""
    window = _window(gap_s=1.0, defender_tyre_age=25.0, degradation_s_per_lap=0.15)
    assert window.undercut_works is True
    assert window.verdict == "undercut"


def test_undercut_fails_against_fresh_tyres() -> None:
    """No accumulated degradation to exploit means nothing to gain."""
    window = _window(gap_s=2.5, defender_tyre_age=1.0, degradation_s_per_lap=0.05)
    assert window.undercut_works is False
    assert window.verdict == "hold"


def test_marginal_call_is_reported_as_marginal() -> None:
    """Within half a second either way is not a decision the model should make."""
    window = _window(gap_s=1.2, defender_tyre_age=5.0, degradation_s_per_lap=0.02)
    assert window.verdict == "marginal"


def test_gain_never_goes_negative() -> None:
    """A fresh tyre is never slower than a worn one, whatever the inputs say."""
    window = _window(degradation_s_per_lap=-0.5, defender_tyre_age=30.0)
    assert window.gain_per_lap_s >= 0.0


def test_scan_finds_only_close_battles() -> None:
    """Cars far apart are not undercut candidates; listing them is noise."""
    laps = pl.DataFrame(
        [
            {
                "session_id": 1,
                "driver_number": str(40 + d),
                "lap_number": 10,
                "position": float(d + 1),
                "gap_ahead_s": None if d == 0 else (0.8 if d == 1 else 25.0),
                "tyre_life": 15.0,
            }
            for d in range(3)
        ]
    )
    windows = undercut.scan_session(
        laps, session_id=1, degradation_s_per_lap=0.1, net_pit_loss_s=22.0
    )
    assert len(windows) == 1
    assert windows[0].gap_s == pytest.approx(0.8)


def test_scan_of_empty_session_is_empty() -> None:
    assert undercut.scan_session(
        pl.DataFrame(), session_id=1, degradation_s_per_lap=0.1, net_pit_loss_s=22.0
    ) == []


def test_negative_degradation_is_never_a_benefit() -> None:
    """Tyres do not get faster with age.

    A negative fitted slope means the fuel correction over-corrected, which happens
    at low-degradation circuits. Left unclamped it would make long stints look
    beneficial and drive the optimiser to a one-stop for entirely the wrong reason.
    """
    assert optimiser.degradation_cost(30, -0.02) == 0.0


def test_no_degradation_means_the_minimum_legal_stops() -> None:
    """With nothing to gain from fresh tyres, only the mandatory stop is worth making."""
    options = optimiser.optimise(
        total_laps=50, slope_s_per_lap=-0.02, net_pit_loss_s=22.0
    )
    assert options[0].n_stops == 1
    assert options[0].degradation_cost_s == 0.0
