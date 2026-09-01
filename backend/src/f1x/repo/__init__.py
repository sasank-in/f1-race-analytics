"""The sole database access boundary for the application."""

from f1x.repo.database import create_session_factory

__all__ = ["create_session_factory"]
