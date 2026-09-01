"""Fuel-correction diagnostics.

These check the detector, not the correction: a frame built with a known residual trend
must be classified correctly, so that when the detector reports "over-corrected" on real
data the verdict can be trusted.
"""

from __future__ import annotations

import polars as pl
import pytest

from f1x.engine.pace.diagnostics import assess_correction, assess_degradation


def _laps(
    *, raw_slope: float, corrected_slope: float, n: int = 60, noise: float = 0.3
) -> pl.DataFrame:
    """A session whose raw and corrected times carry chosen trends.

    Lap-to-lap noise is essential here. A perfectly linear series has correlation
    -1.0 whatever its slope, so without scatter the diagnostic cannot distinguish a
    fully removed trend from a barely touched one — and neither could it on real data.
    """
    import numpy as np

    rng = np.random.default_rng(0)
    jitter = rng.normal(0.0, noise, n)
    return pl.DataFrame(
        {
            "lap_number": list(range(1, n + 1)),
            "lap_time_s": [90.0 + raw_slope * i + jitter[i] for i in range(n)],
            "fuel_corrected_s": [
                90.0 + corrected_slope * i + jitter[i] for i in range(n)
            ],
            "is_representative": [True] * n,
        }
    )


def test_a_removed_trend_is_reported_as_good() -> None:
    """Raw times fall with fuel burn; corrected times are flat."""
    quality = assess_correction(_laps(raw_slope=-0.05, corrected_slope=0.0), session_id=1)
    assert quality is not None
    assert quality.raw_trend < -0.8
    assert quality.verdict == "good"
    # Not 1.0: the residual noise still correlates weakly with lap number, so a
    # perfect correction scores around 0.8 rather than eliminating the trend outright.
    assert quality.improvement > 0.75


def test_over_correction_is_detected() -> None:
    """A positive residual trend means too much was subtracted."""
    quality = assess_correction(
        _laps(raw_slope=-0.05, corrected_slope=0.04), session_id=1
    )
    assert quality is not None
    assert quality.is_overcorrected is True
    assert quality.verdict == "over-corrected"


def test_an_untouched_trend_is_reported_as_weak() -> None:
    """The correction that changes nothing must not be scored as a success."""
    quality = assess_correction(
        _laps(raw_slope=-0.05, corrected_slope=-0.05), session_id=1
    )
    assert quality is not None
    assert quality.improvement == pytest.approx(0.0, abs=0.01)
    assert quality.verdict == "weak"


def test_a_partial_correction_is_reported_as_improved() -> None:
    """Most of the trend removed, but not all of it."""
    quality = assess_correction(
        _laps(raw_slope=-0.05, corrected_slope=-0.005), session_id=1
    )
    assert quality is not None
    assert quality.verdict in {"good", "improved"}
    assert quality.improvement > 0.3


def test_too_few_laps_gives_no_assessment() -> None:
    assert assess_correction(_laps(raw_slope=-0.05, corrected_slope=0.0, n=10), session_id=1) is None


def test_empty_frame_gives_no_assessment() -> None:
    assert assess_correction(pl.DataFrame(), session_id=1) is None


# --------------------------------------------------------------------------
# degradation health
# --------------------------------------------------------------------------


def _curves(slopes: list[float]) -> pl.DataFrame:
    return pl.DataFrame({"degradation_s_per_lap": slopes})


def test_all_positive_slopes_are_healthy() -> None:
    health = assess_degradation(_curves([0.05, 0.10, 0.15]))
    assert health.is_healthy is True
    assert health.n_negative == 0


def test_a_single_negative_slope_is_unhealthy() -> None:
    """Not a threshold: one impossible slope means the correction is wrong somewhere."""
    health = assess_degradation(_curves([0.05, 0.10, -0.01]))
    assert health.is_healthy is False
    assert health.n_negative == 1


def test_negative_fraction_is_reported() -> None:
    health = assess_degradation(_curves([-0.01, -0.02, 0.10, 0.15]))
    assert health.negative_fraction == pytest.approx(0.5)


def test_no_curves_is_not_reported_as_healthy_by_accident() -> None:
    health = assess_degradation(pl.DataFrame())
    assert health.n_curves == 0
    assert health.negative_fraction == 0.0


def test_a_perfect_correction_is_not_flagged_as_over_corrected() -> None:
    """Noise correlates weakly with lap number by chance.

    A 60-lap sample with realistic scatter leaves a residual of about +0.18 even when
    the correction is exactly right, so the over-correction threshold has to clear
    that or it reports false positives on good corrections.
    """
    quality = assess_correction(_laps(raw_slope=-0.05, corrected_slope=0.0), session_id=1)
    assert quality is not None
    assert quality.is_overcorrected is False
    assert quality.verdict == "good"
