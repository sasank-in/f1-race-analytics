"""The published race calendar, for choosing what to fetch.

Every other read in this application answers "what is in the database". This one
answers "what exists" — the calendar comes from the archive, not from `core.events`,
so a season nobody has ingested still lists all of its races.

Each entry carries whether it is already ingested and whether it has been run yet, so
the caller can tell a race worth fetching from one that is already local and one that
has not happened.
"""

from __future__ import annotations

import datetime as dt
import logging
from dataclasses import dataclass

from f1x.config import Settings, get_settings
from f1x.ingest.exceptions import IngestionError

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ScheduledRace:
    """One round on a calendar, and its status relative to the local database."""

    season: int
    round_number: int
    name: str
    country: str | None
    event_date: dt.date | None
    #: False for a race still in the future, which cannot be fetched.
    has_run: bool
    #: True when this round already has a race session stored locally.
    is_ingested: bool
    session_id: int | None = None


def _as_date(value: object) -> dt.date | None:
    """FastF1 hands back a pandas Timestamp, or NaT for an undated entry."""
    if value is None:
        return None
    for attr in ("date",):
        method = getattr(value, attr, None)
        if callable(method):
            try:
                return method()  # type: ignore[no-any-return]
            except (ValueError, AttributeError):
                return None
    return value if isinstance(value, dt.date) else None


def fetch_schedule(season: int, settings: Settings | None = None) -> list[ScheduledRace]:
    """The published calendar for one season, without local status attached.

    Testing events are excluded: they have no round number and cannot be analysed as
    races.
    """
    settings = settings or get_settings()
    try:
        import fastf1

        settings.fastf1_cache_dir.mkdir(parents=True, exist_ok=True)
        fastf1.Cache.enable_cache(str(settings.fastf1_cache_dir))
        schedule = fastf1.get_event_schedule(season, include_testing=False)
    except Exception as exc:
        raise IngestionError(f"could not load the {season} calendar: {exc}") from exc

    today = dt.date.today()
    races: list[ScheduledRace] = []
    for _, row in schedule.iterrows():
        round_number = int(row["RoundNumber"])
        if round_number < 1:  # Defensive: testing should already be excluded.
            continue
        event_date = _as_date(row.get("EventDate"))
        races.append(
            ScheduledRace(
                season=season,
                round_number=round_number,
                name=str(row["EventName"]),
                country=str(row["Country"]) if row.get("Country") is not None else None,
                event_date=event_date,
                # An undated race is treated as runnable rather than hidden: better to
                # let a fetch fail with a clear message than to silently omit a round.
                has_run=event_date is None or event_date <= today,
                is_ingested=False,
            )
        )
    return races


def annotate_ingested(races: list[ScheduledRace], engine: object) -> list[ScheduledRace]:
    """Mark which rounds are already stored, in one query rather than one per race."""
    from sqlalchemy import text

    if not races:
        return races
    season = races[0].season

    with engine.connect() as conn:  # type: ignore[attr-defined]
        rows = conn.execute(
            text(
                "SELECT e.round, s.id FROM core.sessions s "
                "JOIN core.events e ON e.id = s.event_id "
                "WHERE e.season_year = :season AND s.kind = 'R'"
            ),
            {"season": season},
        ).all()
    stored = {int(round_number): int(session_id) for round_number, session_id in rows}

    return [
        ScheduledRace(
            season=race.season,
            round_number=race.round_number,
            name=race.name,
            country=race.country,
            event_date=race.event_date,
            has_run=race.has_run,
            is_ingested=race.round_number in stored,
            session_id=stored.get(race.round_number),
        )
        for race in races
    ]
