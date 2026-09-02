# F1 Race Analysis Engine

A motorsport analytics platform that reconstructs what happened in a Formula 1 session —
and what would have happened under different decisions.

It ingests the official F1 Live Timing feed, normalises it into a time-series warehouse,
and runs a layered engine over it: fuel-corrected pace, tyre degradation, undercut
windows, corner-by-corner telemetry, and Monte Carlo race and championship simulation.

> **The premise.** A lap time is not pace. A lap on lap 3 with 100 kg of fuel, in
> traffic, on a green track is not comparable to a lap on lap 40 — and nothing in the
> source data corrects for that. The engine does, and everything downstream depends on
> those corrections being right.

---

## Capabilities

| Area | What it produces |
|---|---|
| **Pace** | Fuel-corrected lap times, clean-air detection, stint regression separating pace from degradation |
| **Degradation** | Compound-specific curves with interquartile spread, warm-up exclusion, physical-plausibility bounds |
| **Strategy** | Pit-loss estimation, optimal stop count, undercut and overcut windows |
| **Telemetry** | Distance-aligned lap comparison, cumulative delta time, corner detection, track maps |
| **Simulation** | Monte Carlo race outcomes, championship projection |
| **Ratings** | Teammate head-to-head, composite driver ratings across four components |

## Current dataset

| | |
|---|---|
| Seasons | 2022, 2023 |
| Races | 44 |
| Laps | 47,997 |
| Telemetry samples | 16.2 M |
| Position samples | 16.6 M |
| API endpoints | 16 |
| Tests | 259 |

---

## Architecture

```
FastF1 Live Timing ──▶ Polars transform ──▶ PostgreSQL + TimescaleDB
                                                      │
                                        ┌─────────────┴─────────────┐
                                        │      Analysis engine      │
                                        │  pure functions, no I/O   │
                                        └─────────────┬─────────────┘
                                                      ▼
                                          FastAPI ──▶ Next.js
```

| Layer | Technology |
|---|---|
| Data source | FastF1 3.8 (Live Timing), Jolpica (pre-2018 results) |
| Transform | Polars |
| Storage | PostgreSQL 16 + TimescaleDB 2.29 |
| Cache | Redis 7 |
| API | FastAPI, Pydantic v2 |
| UI | Next.js 16, TypeScript |

### Design principles

**The engine is pure.** Analysis functions take DataFrames and return DataFrames. No
database or HTTP access inside `engine/`, so every metric is testable against recorded
fixtures rather than a live session.

**Raw data is immutable.** Ingested payloads land in `raw` and are never edited. Every
derived value is reproducible by re-running the transform.

**Everything is versioned.** Derived rows carry the `engine_version` that produced them,
so changing a model invalidates its cached results instead of silently mixing two
definitions in one chart.

**Estimates carry their caveats.** Where a value is modelled rather than measured, the
API field says so and the UI repeats it. A number that travels without its provenance
gets treated as fact.

---

## Quick start

**Windows** — one command brings up all four services and opens the UI:

```
start.bat
```

**Manual setup:**

```bash
# 1. Database and cache
docker compose -f docker/docker-compose.yml up -d

# 2. Python environment
py -3.12 -m venv .venv
.venv/Scripts/python.exe -m pip install -e "backend[dev]"
cp .env.example .env

# 3. Schema
cd backend && ../.venv/Scripts/python.exe -m alembic upgrade head && cd ..

# 4. Frontend
cd frontend && npm install && cd ..

# 5. Verify
.venv/Scripts/python.exe -m f1x.cli doctor
```

PostgreSQL maps to host port **5433** to avoid colliding with a local install. Docker
runs the database only — Python and Next.js run natively, so debugging and hot reload
behave normally.

### Loading data

```bash
.venv/Scripts/python.exe -m f1x.cli ingest backfill 2023 --last-round 22
.venv/Scripts/python.exe -m f1x.cli transform all
.venv/Scripts/python.exe -m f1x.cli analyse all
```

Telemetry roughly triples ingest time. Use `--no-telemetry` for a timing-only load, then
re-run without the flag to add traces.

---

## Repository layout

```
backend/src/f1x/
  ingest/       FastF1 client, session loader, quality gates
  transform/    validity, corrections, stint and pit-stop derivation
  engine/       pace, degradation, strategy, telemetry, simulation, metrics
  api/          FastAPI routers and response schemas
  models/       SQLAlchemy ORM
db/migrations/  Alembic revisions
frontend/src/   Next.js app, generated API client
tests/          unit (fixtures) and integration (live database)
docs/           architecture, methodology, data model, operations
```

---

## Development

```bash
pytest tests                        # everything
pytest tests -m "not integration"   # unit only, no database
ruff check backend/src tests
mypy backend/src/f1x
cd frontend && npx tsc --noEmit
```

Unit tests run against recorded fixtures and never touch the database. Integration tests
are marked `integration` and fail loudly rather than skipping when the database is
unreachable — a silent skip makes an outage look like a green run.

---

## Documentation

| Document | Contents |
|---|---|
| [Architecture](docs/architecture.md) | System design and phase roadmap |
| [Methodology](docs/methodology.md) | How each figure is derived, and what it claims |
| [Data model](docs/data-model.md) | Schema, hypertables, source-data quirks |
| [Operations](docs/operations.md) | Ingestion, migrations, running the services |
| [Fuel and degradation](docs/fuel-and-degradation-methodology.md) | Resolution plan for the two modelling limits |

---

## Known limits

**Telemetry coverage starts at 2018.** A hard limit of the Live Timing archive, not a
design choice. Earlier seasons are results-and-schedule only.

**The fuel coefficient is a published 0.030 s/kg prior, not fitted.** It cannot be fitted
from race data: `fuel_load_kg` is derived from lap number, so the two are collinear by
construction. Three approaches were tested and all failed for the same reason, with the
measurements recorded in [docs/methodology.md](docs/methodology.md). Fitting it properly
needs practice long-runs, where teams vary fuel independently of race progress.

**Some degradation fits return a negative slope.** Short stints that never clear the tyre
warm-up phase cannot support an estimate. These are flagged `is_physical = false`,
preserved for diagnosis, and clamped to zero downstream so an optimiser can never treat
them as a benefit.

**Degradation and strategy are inferred, not measured.** They reconstruct what the timing
data supports. Tyre state, fuel load and engine modes are never observed directly.

---

## Project status

| Phase | Scope | State |
|---|---|---|
| 0 | Tooling, Docker stack, CI, test harness | Complete |
| 1 | Schema, migrations, hypertables | Complete |
| 2 | Ingestion and quality gates | Complete |
| 3 | Transform: validity, stints, corrections | Complete |
| 4 | Engine: pace and degradation | Complete |
| 5 | Engine: strategy and pit loss | Complete |
| 6 | Engine: telemetry and corners | Complete |
| 7 | Engine: simulation | Complete |
| 8 | Engine: prediction and ratings | Complete |
| 9 | FastAPI service and typed client | Complete |
| 10 | Next.js UI | Complete |
| 11 | Orchestration, incremental refresh, deploy | Planned |
