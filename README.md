# F1 Race Analysis Engine

A motorsport analytics platform that reconstructs what actually happened in a Formula 1
session — and what would have happened under different decisions.

It ingests the official F1 Live Timing feed, normalises it into a time-series warehouse,
and runs a layered engine over it: fuel-corrected pace, tyre degradation curves, undercut
windows, corner-by-corner telemetry deltas, and Monte Carlo race and championship
simulation.

The distinction that drives the design: a lap time is not pace. A lap on lap 3 with 100 kg
of fuel, in traffic, on a green track is not comparable to a lap on lap 40 — and nothing
in the source data corrects for that. The engine does, and everything downstream depends
on those corrections being right.

See [ARCHITECTURE.md](ARCHITECTURE.md) for the full design and phase roadmap.

## Analytical capability

**Pace** — Lap times are corrected for fuel load, track evolution and traffic before any
comparison is made. Stint regression separates a driver's underlying pace (intercept) from
their tyre degradation (slope). Teammate deltas normalise for machinery; clean-air
detection removes laps spent following.

**Degradation** — Compound-specific curves with cliff detection, separating thermal
degradation from wear using track temperature and stint length. Pooled cross-stint
regression removes driver and traffic effects. Output feeds the strategy layer directly.

**Strategy** — Pit-lane time loss modelled per circuit, then undercut and overcut windows
computed against any rival on any lap. An optimal-stop solver evaluates the strategy tree
across compounds and stop counts; safety-car counterfactuals value the opportunities that
did and did not appear. Actual-versus-optimal scoring shows where a race was won or lost.

**Telemetry** — Distance-resampled trace alignment between any two laps, with cumulative
delta-time showing exactly where time is gained. Corners are detected from the curvature
of positional data rather than hardcoded per circuit, giving per-corner minimum speeds,
braking points and throttle-application points. Racing lines compare directly from GPS.

**Simulation** — Monte Carlo race simulation combining degradation models, pit loss,
safety-car probability and a traffic model. Championship projection over the remaining
calendar. Sensitivity analysis on every assumption.

**Prediction** — Qualifying and race outcome models over a historical feature store, with
a backtesting harness and calibration metrics rather than accuracy claims.

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

| layer | choice |
|---|---|
| Data source | FastF1 3.8 (Live Timing telemetry) + Jolpica (pre-2018 results) |
| Transform | Polars |
| Storage | PostgreSQL 16 + TimescaleDB 2.29 |
| Cache | Redis 7 |
| API | FastAPI + Pydantic v2 |
| UI | Next.js 15 |

Three principles hold the design together:

**The engine is pure.** Analysis functions take DataFrames and return DataFrames. No
database or HTTP access inside `engine/`, so every metric is testable against recorded
fixtures rather than a live session.

**Raw data is immutable.** Ingested payloads land in `raw` and are never edited. Every
derived value is reproducible by re-running the transform.

**Everything is versioned.** Derived rows carry the `engine_version` that produced them,
so changing a degradation model invalidates its cached results instead of silently mixing
two definitions in one chart.

## Data model

| schema | contents |
|---|---|
| `raw` | immutable landing zone for ingested payloads |
| `core` | conformed relational tables and telemetry hypertables |
| `mart` | engine output, stamped with `engine_version` |

`core` holds only what a source reports — sessions, laps, stints, pit stops, results, and
four hypertables for telemetry, positions, weather and race control. Anything computed
lives in `mart` and can be dropped and rebuilt.

### Why TimescaleDB

One grand prix produces roughly **721,000 car-telemetry rows** across twenty drivers, and
a similar volume of positional samples. A full season is on the order of **175 million
rows**. At that scale, column width and chunk layout stop being details.

| hypertable | chunk interval | compression |
|---|---|---|
| `core.telemetry` | 1 day | after 7 days |
| `core.positions` | 1 day | after 7 days |
| `core.weather` | 7 days | — |
| `core.race_control` | 7 days | — |

Compression segments by `(session_id, driver_number)`, which is the exact order the
telemetry engine reads a driver's trace. `mart.telemetry_lap_summary` is a continuous
aggregate carrying per-minute speed, throttle, braking and DRS summaries, so a dashboard
never scans raw 4 Hz data to render a summary statistic.

### Source data notes

Two details in the feed shape the schema more than they might appear to:

- `track_status` is **concatenated codes**, not a single value — `'2671'` means four
  different statuses applied during one lap. Stored verbatim, decoded during transform.
- Lap and sector times are stored as **float seconds**, not `INTERVAL`. Every regression
  and delta in the engine operates on floats, so `INTERVAL` would force a cast on every
  read of the hottest columns in the system.

## Quick start

```bash
# 1. database and cache
docker compose -f docker/docker-compose.yml up -d

# 2. environment
py -3.12 -m venv .venv
.venv/Scripts/python.exe -m pip install -e "backend[dev]"
cp .env.example .env

# 3. schema
cd backend && ../.venv/Scripts/python.exe -m alembic upgrade head && cd ..

# 4. verify
.venv/Scripts/python.exe -m f1x.cli doctor
.venv/Scripts/python.exe -m f1x.cli db status
```

`f1x doctor` checks PostgreSQL, the TimescaleDB extension, the three schemas, Redis and
the FastF1 cache, reporting every failure rather than stopping at the first.

Docker runs the **database only** — Python and Next.js run natively, so debugging and hot
reload behave normally. PostgreSQL maps to host port **5433** to avoid colliding with a
local PostgreSQL install; change `DB_PORT` in `.env` if that does not apply.

## Development

```bash
pytest tests                      # everything
pytest tests -m "not integration" # unit only, no database required
ruff check backend/src tests
mypy backend/src/f1x
```

Unit tests run against recorded fixtures and never touch the database. Integration tests
are marked `integration`.

### Migrations

```bash
cd backend
../.venv/Scripts/python.exe -m alembic upgrade head     # apply
../.venv/Scripts/python.exe -m alembic check            # detect model/database drift
../.venv/Scripts/python.exe -m alembic downgrade base   # tear down
```

Autogenerate is configured to ignore TimescaleDB's internal schemas and the
`<table>_ts_idx` indexes that `create_hypertable` creates on its own. Without those
filters Alembic proposes dropping them, which would quietly remove the index behind every
time-range query.

## Coverage and limits

Car telemetry and positional data exist from **2018** onward — this is a hard limit of the
Live Timing archive, not a design choice. Earlier seasons are results-and-schedule only,
sourced from Jolpica. Models that depend on telemetry features therefore train on roughly
seven seasons; 2018–2019 additionally have gaps in positional data.

Degradation and strategy outputs are estimates from observed lap times, not team
telemetry. They reconstruct what the data supports — tyre state, fuel load and engine
modes are inferred, never measured.

## Status

| phase | scope | state |
|---|---|---|
| 0 | Tooling, Docker stack, config, CI, test harness | done |
| 1 | Schema, migrations, hypertables, continuous aggregates | done |
| 2 | Ingestion: FastF1 client, session loader, backfill, QA gates | next |
| 3 | Transform: validity, stints, pit stops, clean air | |
| 4 | Engine: pace and degradation | |
| 5 | Engine: strategy and pit loss | |
| 6 | Engine: telemetry and corners | |
| 7 | Engine: simulation | |
| 8 | Engine: predictive models and composite ratings | |
| 9 | FastAPI service, caching, typed client | |
| 10 | Next.js UI | |
| 11 | Orchestration, incremental refresh, deploy | |
