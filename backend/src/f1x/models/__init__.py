"""ORM models. Importing this package registers every table on `Base.metadata`."""

from f1x.models.base import Base
from f1x.models.core import (
    Circuit,
    Driver,
    Entry,
    Event,
    Lap,
    PitStop,
    Result,
    Season,
    Session,
    Stint,
    Team,
)
from f1x.models.mart import LapMetric
from f1x.models.timeseries import Position, RaceControl, Telemetry, Weather

# Tables converted to hypertables by the migration, with the column to partition on
# and the chunk interval. Telemetry chunks are sized so one chunk holds roughly one
# session; weather and race control are tiny, so a wider interval avoids chunk sprawl.
HYPERTABLES: dict[str, tuple[str, str]] = {
    "core.telemetry": ("ts", "1 day"),
    "core.positions": ("ts", "1 day"),
    "core.weather": ("ts", "7 days"),
    "core.race_control": ("ts", "7 days"),
}

__all__ = [
    "HYPERTABLES",
    "Base",
    "Circuit",
    "Driver",
    "Entry",
    "Event",
    "Lap",
    "LapMetric",
    "PitStop",
    "Position",
    "RaceControl",
    "Result",
    "Season",
    "Session",
    "Stint",
    "Team",
    "Telemetry",
    "Weather",
]
