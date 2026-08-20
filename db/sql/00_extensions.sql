-- Runs once, on first container start, before any migration.
-- Alembic owns the schema; this file only guarantees extensions and namespaces exist.

CREATE EXTENSION IF NOT EXISTS timescaledb;
CREATE EXTENSION IF NOT EXISTS pg_stat_statements;

-- Immutable landing zone for ingested payloads. Never updated in place.
CREATE SCHEMA IF NOT EXISTS raw;

-- Conformed relational + hypertable model.
CREATE SCHEMA IF NOT EXISTS core;

-- Engine output: materialised metrics, stamped with engine_version.
CREATE SCHEMA IF NOT EXISTS mart;
