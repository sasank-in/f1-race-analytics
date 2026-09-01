"""Database construction lives here so ingestion and future APIs share one boundary."""

from __future__ import annotations

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker

from f1x.config import Settings


def create_session_factory(settings: Settings) -> tuple[Engine, sessionmaker[Session]]:
    """Create a short-lived transactional-session factory for application commands."""
    engine = create_engine(str(settings.database_url), pool_pre_ping=True)
    return engine, sessionmaker(engine, expire_on_commit=False)
