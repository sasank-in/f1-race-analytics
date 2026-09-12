# Operations

Running the pipeline and the services.

## Ingestion

Phase 2 provides a validated, repeatable FastF1 ingestion command. It records an
append-only source manifest in `raw.ingest_runs` before replacing the corresponding
conformed session rows in `core`.

```bash
# One race, without the large telemetry/position traces
.venv/Scripts/python.exe -m f1x.cli ingest session 2024 1 R --no-telemetry

# A contiguous range of race sessions (continue after individual failures)
.venv/Scripts/python.exe -m f1x.cli ingest backfill 2024 --last-round 24
```

The default loads telemetry. Use `--no-telemetry` for a quick timing, results, weather,
and race-control load; rerun the same session without that flag to populate telemetry.

## Migrations

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
| 2 | Ingestion: FastF1 client, session loader, backfill, QA gates | done |
| 3 | Transform: validity, stints, pit stops, clean air | done |
| 4 | Engine: pace and degradation | done |
| 5 | Engine: strategy and pit loss | done |
| 6 | Engine: telemetry and corners | done |
| 7 | Engine: simulation | done |
| 8 | Engine: predictive models and composite ratings | done |
| 9 | FastAPI service, caching, typed client | done |
| 10 | Next.js UI | done |
| 11 | Orchestration, incremental refresh, deploy | next |

## UI

```bash
cd frontend && npm install && npm run dev     # http://localhost:3000
```

| Page | Question it answers |
|---|---|
| `/` | Which of the loaded races is worth opening? |
| `/sessions/{id}` | Who actually had the pace, and how did the tyres fall away? |
| `/sessions/{id}/strategy` | Where were the stops, and where was an undercut available? |
| `/sessions/{id}/telemetry` | Where on the lap did the time go? |
| `/season` | Which circuits punish tyres, and whose pace moved through the year? |
| `/teammates` | Which driver beat the other in the same car? |
| `/ratings` | How do drivers compare once pace, racecraft, consistency and tyres are separated? |
| `/fetch` | What else can I analyse, and can I pull it in now? |


The client is generated from the API's OpenAPI schema, so a field renamed in a Pydantic
model surfaces as a TypeScript compile error rather than an undefined value in a chart.

**Colour is validated, not chosen.** The categorical palette is checked with the
data-viz validator in both modes — worst adjacent CVD ΔE 9.1 light and 8.4 dark, both
above the 8.0 target. Light mode raises a contrast warning on two slots, and the
obligation that creates is visible labels, so every series is direct-labelled and
identity never rests on hue alone.

Tyre compounds are the deliberate exception. Soft, medium and hard are red, yellow and
grey by F1 convention; grey fails the chroma floor, and substituting a "better" palette
would confuse anyone who watches the sport. Compounds are therefore always shown with
their name beside the swatch.

**The caveats travel to the screen.** Each card states what its numbers are — pace is
the 20th percentile of clean fuel-corrected laps, not a fastest lap; pit loss is what
the in-lap and out-lap add beyond two normal laps, not pit-lane transit. A session where
some stint fits came back negative says so, with the count, rather than quietly dropping
them.


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

## API reference

Twenty-three endpoints under `/api/v1`, plus `/health`, which reports database
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
| `GET /insights/{id}` | Winner, podium, and what was notable about the race |
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

## Project layout

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
