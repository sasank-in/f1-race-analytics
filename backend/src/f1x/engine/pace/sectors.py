"""Where on the lap a car was quick.

Pace ranking says which car was fastest over a lap. It cannot say *where* — and two
cars a tenth apart on total time can be built completely differently: one strong in
the slow technical section, the other carrying speed through the fast sweepers.

Sector times answer that, and they have been ingested since Phase 2 without anything
reading them. This module turns them into a per-driver profile: each sector as a gap
to the best car in that sector, so a row reads as a shape rather than three numbers.

Same quantile as the pace ranking, deliberately. Taking the 20th percentile rather
than the minimum keeps one exceptional sector from defining a driver, and matching
`ranking.PACE_QUANTILE` means the sector gaps and the overall pace gap describe the
same laps rather than two different samples.
"""

from __future__ import annotations

from dataclasses import dataclass

import polars as pl

from f1x.engine.pace.ranking import PACE_QUANTILE

#: A driver needs this many timed laps before a sector profile means anything.
MIN_LAPS_FOR_SECTORS = 5


@dataclass(frozen=True)
class SectorProfile:
    """One driver's strength in each sector, relative to the best car there."""

    driver_number: str
    n_laps: int

    sector1_s: float
    sector2_s: float
    sector3_s: float

    # Gap to the quickest car in that sector. Zero means they set the benchmark.
    gap1_s: float
    gap2_s: float
    gap3_s: float

    @property
    def total_s(self) -> float:
        return self.sector1_s + self.sector2_s + self.sector3_s

    @property
    def strongest_sector(self) -> int:
        """The sector where this driver is closest to the benchmark (1, 2 or 3)."""
        gaps = (self.gap1_s, self.gap2_s, self.gap3_s)
        return gaps.index(min(gaps)) + 1

    @property
    def weakest_sector(self) -> int:
        gaps = (self.gap1_s, self.gap2_s, self.gap3_s)
        return gaps.index(max(gaps)) + 1

    @property
    def spread_s(self) -> float:
        """How unevenly the deficit is distributed across the lap.

        A car 0.3s off everywhere is simply slower. A car level in two sectors and
        0.3s down in one has a specific weakness, and that is the interesting case —
        so the spread is what separates "slow" from "shaped".
        """
        return max(self.gap1_s, self.gap2_s, self.gap3_s) - min(
            self.gap1_s, self.gap2_s, self.gap3_s
        )


def build_sector_profiles(laps: pl.DataFrame) -> list[SectorProfile]:
    """Per-driver sector profiles from a session's laps.

    ``laps`` needs ``driver_number`` and the three sector columns. Laps missing any
    sector are dropped rather than partially used: a profile built from three
    different lap samples would not describe one lap.
    """
    required = {"driver_number", "sector1_s", "sector2_s", "sector3_s"}
    if laps.is_empty() or not required <= set(laps.columns):
        return []

    usable = laps.drop_nulls(["sector1_s", "sector2_s", "sector3_s"])
    if usable.is_empty():
        return []

    aggregated = (
        usable.group_by("driver_number")
        .agg(
            n_laps=pl.len(),
            s1=pl.col("sector1_s").quantile(PACE_QUANTILE),
            s2=pl.col("sector2_s").quantile(PACE_QUANTILE),
            s3=pl.col("sector3_s").quantile(PACE_QUANTILE),
        )
        .filter(pl.col("n_laps") >= MIN_LAPS_FOR_SECTORS)
    )
    if aggregated.is_empty():
        return []

    # The benchmark in each sector is set independently: the quickest S1 and the
    # quickest S2 are often different cars, which is the whole point of the view.
    best1 = aggregated.get_column("s1").min()
    best2 = aggregated.get_column("s2").min()
    best3 = aggregated.get_column("s3").min()

    profiles = [
        SectorProfile(
            driver_number=str(row["driver_number"]),
            n_laps=int(row["n_laps"]),
            sector1_s=float(row["s1"]),
            sector2_s=float(row["s2"]),
            sector3_s=float(row["s3"]),
            gap1_s=float(row["s1"]) - float(best1),  # type: ignore[arg-type]
            gap2_s=float(row["s2"]) - float(best2),  # type: ignore[arg-type]
            gap3_s=float(row["s3"]) - float(best3),  # type: ignore[arg-type]
        )
        for row in aggregated.to_dicts()
    ]
    return sorted(profiles, key=lambda p: p.total_s)
