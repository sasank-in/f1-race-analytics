"""Immutable source-audit tables in the ``raw`` schema.

These are SQLAlchemy Core tables rather than ORM entities: raw payloads are append-only
audit records and are never part of the application's mutable domain model.
"""

from __future__ import annotations

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Column,
    DateTime,
    Identity,
    Index,
    SmallInteger,
    String,
    Table,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB

from f1x.models.base import Base

ingest_runs = Table(
    "ingest_runs",
    Base.metadata,
    Column("id", BigInteger, Identity(), primary_key=True),
    Column("source", String(32), nullable=False),
    Column("season_year", SmallInteger, nullable=False),
    Column("round_number", SmallInteger, nullable=False),
    Column("session_kind", String(3), nullable=False),
    Column("retrieved_at", DateTime(timezone=True), server_default=text("now()"), nullable=False),
    Column("payload", JSONB, nullable=False),
    Column("payload_sha256", String(64), nullable=False),
    CheckConstraint("round_number > 0", name="round_number_positive"),
    Index("ix_ingest_runs_session", "season_year", "round_number", "session_kind", "retrieved_at"),
    schema="raw",
    comment="Append-only FastF1 source manifests; never updated in place",
)
