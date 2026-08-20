"""Declarative base and shared column types."""

from __future__ import annotations

import datetime as dt
from typing import Annotated

from sqlalchemy import BigInteger, Integer, MetaData, SmallInteger, String, text
from sqlalchemy.orm import DeclarativeBase, mapped_column

# Explicit naming so Alembic autogenerate produces stable, reviewable constraint names.
NAMING_CONVENTION = {
    "ix": "ix_%(table_name)s_%(column_0_N_name)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_N_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION, schema="core")


# --- column aliases ------------------------------------------------------
int_pk = Annotated[int, mapped_column(Integer, primary_key=True)]
bigint_pk = Annotated[int, mapped_column(BigInteger, primary_key=True, autoincrement=True)]
small = Annotated[int, mapped_column(SmallInteger)]
code3 = Annotated[str, mapped_column(String(3))]

# Lap and sector times are stored as seconds (double precision), not INTERVAL.
# FastF1 gives timedeltas, but every downstream regression, delta and mean operates on
# floats — storing seconds avoids a cast on every read of the hottest columns.

timestamptz = Annotated[
    dt.datetime,
    mapped_column(server_default=text("now()")),
]
