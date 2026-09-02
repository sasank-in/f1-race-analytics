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

Three views: a session index, a per-session dashboard (pace, degradation, strategy,
simulation, stint fits), and season driver ratings.

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
