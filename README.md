# F1 Race Analysis Engine

Advanced Formula 1 analytics: fuel-corrected pace, tyre degradation modelling, strategy
and undercut analysis, corner-by-corner telemetry comparison, Monte Carlo race and
championship simulation.

See [ARCHITECTURE.md](ARCHITECTURE.md) for the full design and phase roadmap.

## Stack

| layer | choice |
|---|---|
| Data source | FastF1 3.8 (Live Timing telemetry) + Jolpica API (pre-2018 results) |
| Transform | Polars / pandas |
| Storage | PostgreSQL 16 + TimescaleDB 2.29 |
| Cache | Redis 7 |
| API | FastAPI + Pydantic v2 |
| UI | Next.js 15 |

## Quick start

```bash
# 1. start the database and cache
docker compose -f docker/docker-compose.yml up -d

# 2. create the environment
py -3.12 -m venv .venv
.venv/Scripts/python.exe -m pip install -e "backend[dev]"

# 3. copy config
cp .env.example .env

# 4. apply the schema
cd backend && ../.venv/Scripts/python.exe -m alembic upgrade head && cd ..

# 5. verify everything is wired up
.venv/Scripts/python.exe -m f1x.cli doctor
.venv/Scripts/python.exe -m f1x.cli db status
```

`f1x doctor` checks PostgreSQL, the TimescaleDB extension, the `raw`/`core`/`mart`
schemas, Redis, and the FastF1 cache — and tells you what to start if something is down.

## Ports

The container maps PostgreSQL to **5433**, because port 5432 is already taken by a local
PostgreSQL install. Change `DB_PORT` in `.env` if that does not apply to you.

## Tests

```bash
.venv/Scripts/python.exe -m pytest ../tests            # everything
.venv/Scripts/python.exe -m pytest -m "not integration" # unit only, no database needed
```

Unit tests run against recorded fixtures and never touch the database. Integration tests
are marked `integration` and require the Docker stack to be running.

## Schemas

- `raw` — immutable landing zone for ingested payloads, never updated in place
- `core` — conformed relational tables and telemetry hypertables
- `mart` — engine output, each row stamped with the `engine_version` that produced it

## Data coverage

Car telemetry and positional data are available from **2018** onward. Earlier seasons are
results-and-schedule only, sourced from Jolpica.
