"""Alembic environment. The database URL comes from f1x.config, not alembic.ini."""

from __future__ import annotations

from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

from f1x.config import get_settings
from f1x.models import Base

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

config.set_main_option("sqlalchemy.url", str(get_settings().database_url))

target_metadata = Base.metadata

# Timescale keeps its own objects in these schemas; autogenerate must ignore them.
EXCLUDED_SCHEMAS = {"_timescaledb_internal", "_timescaledb_catalog",
                    "_timescaledb_config", "_timescaledb_cache", "timescaledb_information"}


# create_hypertable() builds a "<table>_<timecol>_idx" DESC index on every hypertable.
# Alembic cannot see it in the models and would happily drop it, so it is filtered out.
HYPERTABLE_TABLES = {"telemetry", "positions", "weather", "race_control"}


def include_object(obj, name, type_, reflected, compare_to) -> bool:  # noqa: ARG001
    if getattr(obj, "schema", None) in EXCLUDED_SCHEMAS:
        return False
    if type_ == "index" and reflected:
        table = getattr(obj, "table", None)
        if table is not None and table.name in HYPERTABLE_TABLES and name.endswith("_ts_idx"):
            return False
    return True


def run_migrations_offline() -> None:
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        include_schemas=True,
        include_object=include_object,
        version_table_schema="core",
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            include_schemas=True,
            include_object=include_object,
            version_table_schema="core",
            compare_type=True,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
