# F1 Race Analysis Engine

Formula 1 analytics that separate **how fast a car was** from **where it finished**.

A results table says Alonso beat Leclerc at Bahrain 2023. This engine says Leclerc had
the third-quickest car and retired from it. Across 2022 and 2023, **the quickest car did
not win 18 of 44 races** — and those are the races worth studying.


## The problem it solves

A lap time is not pace. A lap on lap 3 with 100 kg of fuel, in traffic, on a green track
is not comparable to a lap on lap 40 — and nothing in the source data corrects for that.

Public F1 tools plot raw lap times. This one corrects them first: fuel load, track
evolution, traffic, tyre warm-up and lap validity are all removed before any two laps are
compared. Everything downstream — degradation curves, strategy, simulation, ratings —
depends on those corrections being right, so the methodology is documented and the
failures are recorded alongside the successes.

## What it produces

| Area | Output |
|---|---|
| **Pace** | Fuel-corrected lap times with the result beside them, clean-air detection, stint regression separating pace from degradation |
| **Degradation** | Compound curves with interquartile spread, warm-up exclusion, physical bounds from published research |
| **Strategy** | Pit-loss estimation, optimal stop count, undercut and overcut windows lap by lap |
| **Telemetry** | Distance-aligned lap comparison, cumulative delta, corner detection, track maps drawn from GPS |
| **Simulation** | Monte Carlo race outcomes, championship projection |
| **Season** | Circuits ranked by tyre demand, pace curves through a calendar |
| **Drivers** | Teammate head-to-head, composite ratings across four components |

## Worked examples

**Bahrain 2023, pace against result.** Verstappen P1 on pace and P1 on the road.
Leclerc **P3 on pace, DNF**. Norris **P4 on pace, finished P17**. The disagreement is
the analysis.

**Circuits by tyre demand.** Sakhir degrades at 0.143 s/lap and stints last 19 laps;
Spielberg at 0.072 and stints last 29. Nothing enforces that relationship — it emerges.

**Compound ordering.** Across 98 fitted curves: INTERMEDIATE 0.162 > SOFT 0.074 >
MEDIUM 0.055 > HARD 0.048 s/lap. Again unenforced, and the correct physical order.

**Track geometry.** Lap distance integrated from speed alone lands within 0.9–2.2 % of
the true length across eight circuits, erring consistently short — the signature of
trapezoidal integration, not noise.


## Dataset

Figures below are the reference backfill, not a limit. Any race from 2018 onward can be
pulled in on demand from the UI or the command line — 2018 is where lap timing begins,
and the engine needs laps.

| | |
|---|---|
| Seasons backfilled | 2022, 2023 |
| Races | 44 |
| Laps | 47,997 |
| Telemetry samples | 16.2 M |
| Position samples | 16.6 M |
| API endpoints | 22, plus `/health` |
| UI pages | 8 |
| Tests | 282 |


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

### Principles

**The engine is pure.** Analysis functions take DataFrames and return DataFrames. No
database or HTTP inside `engine/`, so every metric is testable against fixtures rather
than a live session.

**Raw data is immutable.** Ingested payloads land in `raw` and are never edited. Every
derived value is reproducible by re-running the transform.

**Everything is versioned.** Derived rows carry the `engine_version` that produced them,
so changing a model invalidates its cached results rather than mixing two definitions in
one chart.

**Estimates carry their caveats.** Where a value is modelled rather than measured, the
API field says so and the UI repeats it. A number that travels without its provenance
gets treated as fact.


## Quick start

**Windows** — one command starts everything and opens the UI:

```
start.bat
```

**Manual:**

```bash
docker compose -f docker/docker-compose.yml up -d          # database and cache
py -3.12 -m venv .venv
.venv/Scripts/python.exe -m pip install -e "backend[dev]"
cp .env.example .env
cd backend && ../.venv/Scripts/python.exe -m alembic upgrade head && cd ..
cd frontend && npm install && cd ..
.venv/Scripts/python.exe -m f1x.cli doctor                 # verify
```

PostgreSQL maps to host port **5433** to avoid colliding with a local install. Docker
runs the database only — Python and Next.js run natively.

### Loading data

The dataset is not fixed at install time. **Fetch** in the UI lists the published
calendar for any season — including seasons with nothing stored locally — and pulls a
race in on demand, running ingest, transform and analyse so it is immediately viewable.

From a terminal, to load in bulk:

```bash
.venv/Scripts/python.exe -m f1x.cli ingest schedule 2024        # what is available
.venv/Scripts/python.exe -m f1x.cli ingest backfill 2023 --last-round 22
.venv/Scripts/python.exe -m f1x.cli transform all
.venv/Scripts/python.exe -m f1x.cli analyse all
```

`ingest session <year> <round> R --analyse` does one race end to end, which is what the
fetch endpoint runs.

Telemetry roughly triples load time. `--no-telemetry` gives a timing-only pass; re-run
without the flag to add traces. Re-loading a race replaces it rather than duplicating it.


## Command reference

Installing the package puts `f1x` on the path, so `f1x doctor` is equivalent to the
longer `.venv/Scripts/python.exe -m f1x.cli doctor` used above. `--help` on any group
lists its options.

| Command | Purpose |
|---|---|
| `doctor` | Check database, cache and FastF1 connectivity before anything else |
| `version` | Print the engine version stamped onto derived rows |
| `db status` / `db rowcounts` | Migration state; row counts per table |
| `ingest schedule <year>` | List a season's calendar and what is already stored |
| `ingest session <year> <round>` | Load one session; `--analyse` runs the full pipeline |
| `ingest backfill <year>` | Load a whole season; `--no-telemetry` for a timing-only pass |
| `transform session` / `transform all` | Derive validity, corrections, stints and pit stops |
| `analyse session` / `analyse all` | Fit pace, degradation and stint models |
| `analyse fuel` | Re-run the fuel-coefficient fit and print its diagnostics |
| `analyse quality` | Report leakage checks and fit health across the dataset |
| `strategy session` | Pit loss, optimal stop count, undercut windows |
| `telemetry compare` | Distance-aligned two-lap comparison with corner deltas |
| `simulate race` / `simulate championship` | Monte Carlo race outcome; title projection |
| `ratings drivers` | Composite driver ratings for a season |
| `api serve` | Run the FastAPI service |
| `api schema` | Write the OpenAPI document the TypeScript client is generated from |
| `api cache-clear` | Drop cached responses after an engine-version change |

## API

Twenty-two endpoints under `/api/v1`, plus `/health`, which reports database
reachability and the engine version. Interactive documentation is at `/docs` once the
service is running.

All but one are reads. `POST /fetch` is the exception: it is the only route that
changes what is stored, and it only ever adds or replaces a race.

| Endpoint | Returns |
|---|---|
| `GET /seasons`, `/events`, `/sessions` | Reference lists |
| `GET /sessions/{id}` | One session |
| `GET /summaries` | Every race with a one-line headline and an upset flag |
| `GET /analysis/laps/{id}` | Laps, optionally representative only |
| `GET /analysis/pace/{id}` | Fuel-corrected pace ranking with finishing result |
| `GET /analysis/degradation/{id}` | Compound degradation curves |
| `GET /strategy/{id}` | Pit loss and optimal stop count |
| `GET /undercut/{id}` | Undercut and overcut windows, lap by lap |
| `GET /stints/{id}` | Stint timeline |
| `GET /simulate/{id}` | Monte Carlo race outcome distribution |
| `GET /ratings/{season}` | Composite driver ratings |
| `GET /teammates/{season}` | Teammate head-to-head deltas |
| `GET /season/circuits` | Circuits ranked by tyre demand |
| `GET /season/pace/{season}` | Per-driver pace curve through a calendar |
| `GET /telemetry/compare/{id}` | Two laps aligned by distance |
| `GET /telemetry/map/{id}` | Track map drawn from GPS, coloured by speed |
| `GET /schedule/{season}` | Published calendar, marking what is stored locally |
| `POST /fetch` | Start an on-demand fetch; returns 202 and a job to poll |
| `GET /fetch` | Recent fetch jobs, newest first |
| `GET /fetch/{id}` | Progress of one fetch |

Every analysis response carries the `engine_version` that produced it. Redis caching is
keyed on that version, so a model change invalidates cached values rather than serving
two definitions from one endpoint.

## Interface

| Page | Question it answers |
|---|---|
| `/` | Which of these 44 races is worth opening? |
| `/sessions/{id}` | Who actually had the pace, and how did the tyres fall away? |
| `/sessions/{id}/strategy` | Where were the stops, and where was an undercut available? |
| `/sessions/{id}/telemetry` | Where on the lap did the time go? |
| `/season` | Which circuits punish tyres, and whose pace moved through the year? |
| `/teammates` | Which driver beat the other in the same car? |
| `/ratings` | How do drivers compare once pace, racecraft, consistency and tyres are separated? |
| `/fetch` | What else can I analyse, and can I pull it in now? |

## Layout

```
backend/src/f1x/
  ingest/       FastF1 client, session loader, quality gates
  transform/    validity, corrections, stint and pit-stop derivation
  engine/       pace, degradation, strategy, telemetry, simulation, metrics
  api/          FastAPI routers and response schemas
  models/       SQLAlchemy ORM
  cli.py        Typer command line
db/migrations/  Alembic revisions
docker/         PostgreSQL + TimescaleDB and Redis compose stack
frontend/src/
  app/          Next.js routes
  components/   charts and layout, hand-drawn SVG rather than a chart library
  api/          client generated from the OpenAPI schema
tests/          unit (fixtures) and integration (live database)
docs/           architecture, methodology, models, data model, operations
```

## Development

```bash
pytest tests                        # everything
pytest tests -m "not integration"   # unit only, no database
ruff check backend/src tests
mypy backend/src/f1x
cd frontend && npx tsc --noEmit
```

Unit tests run against fixtures and never touch the database. Integration tests fail
loudly rather than skipping when the database is unreachable — a silent skip makes an
outage look like a green run — but skip cleanly when the schema is present and empty,
which is how CI runs.

## Documentation

| Document | Contents |
|---|---|
| [Architecture](docs/architecture.md) | System design and phase roadmap |
| [Methodology](docs/methodology.md) | How each figure is derived, and what it claims |
| [Models and training](docs/models-and-training.md) | What is fitted, on what, and what each result is worth |
| [Data model](docs/data-model.md) | Schema, hypertables, source-data quirks |
| [Operations](docs/operations.md) | Ingestion, migrations, running the services |
| [Fuel and degradation](docs/fuel-and-degradation-methodology.md) | The two modelling limits, in detail |


## Known limits

These are stated because a tool that hides its assumptions is harder to trust than one
that names them.

**Telemetry starts at 2018.** A hard limit of the Live Timing archive. Earlier seasons
are results-and-schedule only.

**The fuel coefficient is a published 0.030 s/kg prior, not fitted.** It *cannot* be
fitted from race data: `fuel_load_kg` is derived from lap number, so the two are
collinear by construction. Three approaches were tried — per-circuit, cross-season, and
pooled with fixed effects — and all failed for the same reason. The pooled fit is the
instructive one: it produced a plausible +0.049 s/kg at r² 0.996, then swung to −0.041
on softs and +0.072 on hards under a subset check. Fitting it properly needs practice
long-runs, where teams vary fuel independently of race progress. Measurements are in
[docs/methodology.md](docs/methodology.md).

**Some degradation fits return a negative slope.** Short stints that never clear tyre
warm-up cannot support an estimate. They are flagged `is_physical = false`, preserved for
diagnosis, and clamped to zero downstream so an optimiser can never treat them as a
benefit.

**Degradation and strategy are inferred.** They reconstruct what the timing data
supports. Tyre state, fuel load and engine modes are never observed directly.


## Configuration

Settings load from `.env`; copy `.env.example` and edit. The defaults work for a local
Docker stack.

| Variable | Default | Notes |
|---|---|---|
| `DB_PORT` | `5433` | Host port for PostgreSQL, offset to avoid a local 5432 |
| `POSTGRES_USER` / `_PASSWORD` / `_DB` | `f1x` | Database credentials |
| `REDIS_HOST` / `REDIS_PORT` | `localhost:6379` | Response cache |
| `F1X_DEBUG` | `true` | Verbose logging and API error detail |
| `NEXT_PUBLIC_API_URL` | `http://127.0.0.1:8000` | Where the UI looks for the API |

## Status

Phases 0–10 are complete: tooling, schema, ingestion, transform, five engine layers, the
API, and the UI. Phase 11 — orchestration, incremental refresh and deployment — remains.

Not yet built: qualifying and practice ingestion, sector decomposition, actual-versus-
optimal strategy scoring, and driver style fingerprints. The
[architecture document](docs/architecture.md) tracks the full capability list.

## Licence and attribution

Timing and telemetry data is retrieved through [FastF1](https://github.com/theOehrly/Fast-F1)
from the Formula 1 Live Timing service, and pre-2018 results through
[Jolpica](https://github.com/jolpica/jolpica-f1). This is an unofficial project, not
associated with Formula 1, the FIA, or any team. F1 and Formula 1 are trademarks of
Formula One Licensing BV.

Intended for research and analysis. Use of the underlying data is subject to the terms
of its providers.
