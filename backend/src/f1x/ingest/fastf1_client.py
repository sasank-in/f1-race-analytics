"""A narrow, cache-aware adapter around FastF1.

Keeping this dependency at the edge means loaders can be tested with a recorded object
and the rest of the application never needs to know FastF1's API surface.
"""

# ruff: noqa: ANN401

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from f1x.config import Settings, get_settings
from f1x.ingest.exceptions import IngestionError


@dataclass(frozen=True)
class SessionRequest:
    """A stable identifier for a single event session."""

    year: int
    round_number: int
    kind: str
    telemetry: bool = True

    def __post_init__(self) -> None:
        if self.year < 1950:
            raise ValueError("year must be in the Formula 1 world-championship era")
        if self.round_number < 1:
            raise ValueError("round_number must be positive")
        if self.kind not in {"FP1", "FP2", "FP3", "Q", "SQ", "S", "R"}:
            raise ValueError(f"unsupported session kind: {self.kind}")


class FastF1Client:
    """Load a full FastF1 session, using the repository cache for repeatability."""

    def __init__(self, settings: Settings | None = None) -> None:
        self._settings = settings or get_settings()

    def load(self, request: SessionRequest) -> Any:
        """Fetch and fully load the requested session or raise an actionable error."""
        try:
            import fastf1

            self._settings.fastf1_cache_dir.mkdir(parents=True, exist_ok=True)
            fastf1.Cache.enable_cache(str(self._settings.fastf1_cache_dir))
            session = fastf1.get_session(request.year, request.round_number, request.kind)
            session.load(
                laps=True,
                telemetry=request.telemetry,
                weather=True,
                messages=True,
            )
            return session
        except Exception as exc:
            raise IngestionError(
                f"could not load {request.year} round {request.round_number} {request.kind}: {exc}"
            ) from exc
