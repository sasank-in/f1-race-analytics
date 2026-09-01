"""ingest run audit

Revision ID: e493fb92ac07
Revises: beb9d3fefee0
Create Date: 2026-08-21 10:00:00.000000
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "e493fb92ac07"
down_revision: str | None = "beb9d3fefee0"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # ``raw`` is normally initialised by docker-entrypoint SQL. Creating it here too
    # keeps ``alembic upgrade`` self-contained for fresh CI and production databases.
    op.execute("CREATE SCHEMA IF NOT EXISTS raw")
    op.create_table(
        "ingest_runs",
        sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column("source", sa.String(length=32), nullable=False),
        sa.Column("season_year", sa.SmallInteger(), nullable=False),
        sa.Column("round_number", sa.SmallInteger(), nullable=False),
        sa.Column("session_kind", sa.String(length=3), nullable=False),
        sa.Column("retrieved_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("payload", postgresql.JSONB(), nullable=False),
        sa.Column("payload_sha256", sa.String(length=64), nullable=False),
        sa.CheckConstraint("round_number > 0", name="round_number_positive"),
        schema="raw",
        comment="Append-only FastF1 source manifests; never updated in place",
    )
    op.create_index(
        "ix_ingest_runs_session", "ingest_runs",
        ["season_year", "round_number", "session_kind", "retrieved_at"], schema="raw",
    )


def downgrade() -> None:
    op.drop_index("ix_ingest_runs_session", table_name="ingest_runs", schema="raw")
    op.drop_table("ingest_runs", schema="raw")
