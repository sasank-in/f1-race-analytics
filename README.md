# F1 Race Analysis Engine

Formula 1 analytics that separate **how fast a car was** from **where it finished**.

A results table says Alonso beat Leclerc at Bahrain 2023. This engine says Leclerc had
the third-quickest car and retired from it. Across 2022 and 2023, **the quickest car did
not win 18 of 44 races** — and those are the races worth studying.

![Race pace at Bahrain 2023, with the finishing result beside each car's fuel-corrected
pace](docs/images/race-pace.png)

Leclerc **P3 on pace, DNF**. Norris **P4 on pace, finished P17**. The two columns
disagreeing is the analysis.


## Why raw lap times are not pace

A lap on lap 3 with 100 kg of fuel, in traffic, on a green track is not comparable to a
lap on lap 40. Nothing in the source data corrects for that, so public F1 tools plot lap
times and call it pace.

This engine corrects first — fuel load, track evolution, traffic, tyre warm-up and lap
validity are all removed before any two laps are compared. Everything downstream depends
on those corrections being right, so [the methodology](docs/methodology.md) documents how
each figure is derived, and records the attempts that failed alongside the ones that
worked.


## What you can ask it

**Who actually had the pace?** A ranking built from fuel-corrected clean-air laps, with
each car's finishing position beside it.

**Where did the race turn?** Position lap by lap, so pit stops, overtakes and safety cars
are visible as shapes rather than inferred from a results table.

**Was that undercut on?** Every lap a driver sat within three seconds of the car ahead,
scored against the rival's tyre age and the cost of a stop.

**How did the tyres behave?** Compound degradation curves with their interquartile
spread, fitted after the warm-up phase and bounded by published research.

**Where on the lap was the time?** Two laps aligned by distance, with a cumulative delta
and corner-by-corner comparison, on a track map drawn from GPS.

**What happened across a season?** Circuits ranked by tyre demand, and each driver's pace
through the calendar.

![Pace through the 2023 season: Verstappen pinned to the baseline, Norris climbing as
McLaren's upgrade lands](docs/images/season-pace.png)


## Worked examples

**Compound ordering.** Across 98 fitted curves: INTERMEDIATE 0.162 > SOFT 0.074 >
MEDIUM 0.055 > HARD 0.048 s/lap. Nothing in the code enforces that ordering — it falls
out of the fits, and it is the correct physical order.

**Circuits by tyre demand.** Sakhir degrades at 0.143 s/lap and stints last 19 laps;
Spielberg at 0.072 and stints last 29. That relationship is also unenforced.

**Track geometry.** Lap distance integrated from speed alone lands within 0.9–2.2 % of
the true length across eight circuits, erring consistently short — the signature of
trapezoidal integration, not noise.


## Running it

Docker runs PostgreSQL and Redis; Python and Next.js run natively.

```bash
docker compose -f docker/docker-compose.yml up -d
py -3.12 -m venv .venv
.venv/Scripts/python.exe -m pip install -e "backend[dev]"
cp .env.example .env
cd backend && ../.venv/Scripts/python.exe -m alembic upgrade head && cd ..
cd frontend && npm install && cd ..
```

On Windows, `start.bat` does all of the above and opens the UI. `start.bat --stop` shuts
the services down again.

**Loading races.** The dataset is not fixed at install time — the **Fetch** page lists
the published calendar for any season, including seasons with nothing stored locally, and
pulls a race in on demand. From a terminal:

```bash
.venv/Scripts/python.exe -m f1x.cli ingest schedule 2024                 # what exists
.venv/Scripts/python.exe -m f1x.cli ingest session 2024 1 R --analyse    # one race
```

Telemetry roughly triples load time; `--no-telemetry` skips it. Re-loading a race
replaces it rather than duplicating it. The full command set is in
[docs/operations.md](docs/operations.md).


## How it fits together

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

Four rules shape the code:

**The engine is pure.** Analysis functions take DataFrames and return DataFrames — no
database or HTTP inside `engine/` — so every metric is testable against fixtures rather
than a live session.

**Raw data is immutable.** Ingested payloads land in `raw` and are never edited. Every
derived value is reproducible by re-running the transform.

**Everything is versioned.** Derived rows carry the `engine_version` that produced them,
so changing a model invalidates its cached results instead of mixing two definitions in
one chart.

**Estimates carry their caveats.** Where a value is modelled rather than measured, the
API field says so and the UI repeats it. A number that travels without its provenance
gets treated as fact.

Built on FastF1 3.8, Polars, PostgreSQL 16 with TimescaleDB, Redis, FastAPI and
Next.js 16. Charts are hand-drawn SVG rather than a charting library.


## What it does not claim

A tool that hides its assumptions is harder to trust than one that names them.

**Telemetry starts at 2018** — a hard limit of the Live Timing archive. Earlier seasons
are results-and-schedule only.

**The fuel coefficient is a published 0.030 s/kg prior, not fitted.** It *cannot* be
fitted from race data: `fuel_load_kg` is derived from lap number, so the two are
collinear by construction. Three approaches were tried and all failed the same way. The
pooled fit is the instructive one — a plausible +0.049 s/kg at r² 0.996, which then swung
to −0.041 on softs and +0.072 on hards under a subset check.

**Some degradation fits return a negative slope.** Short stints that never clear tyre
warm-up cannot support an estimate. They are flagged rather than hidden, shown as "no
usable fit", and clamped to zero downstream so an optimiser can never treat them as a
benefit.

**Degradation and strategy are inferred**, reconstructed from what the timing data
supports. Tyre state, fuel load and engine modes are never observed directly.

**One model is deliberately unexposed.** A ridge regression predicting finishing position
exists in `engine/predictive/` with no CLI or API route, because its own quality gate
accepts pure noise in roughly half of trials. The measurements are in
[docs/models-and-training.md](docs/models-and-training.md); it stays unwired until the
gate is fixed.


## Reading further

| Document | What it covers |
|---|---|
| [Methodology](docs/methodology.md) | How each figure is derived, and what it claims |
| [Models and training](docs/models-and-training.md) | What is fitted, on what, and what each result is worth |
| [Fuel and degradation](docs/fuel-and-degradation-methodology.md) | The two modelling limits in full |
| [Architecture](docs/architecture.md) | System design and phase roadmap |
| [Data model](docs/data-model.md) | Schema, hypertables, source-data quirks |
| [Operations](docs/operations.md) | Commands, API and UI reference, configuration, development |


## Status

Phases 0–10 are complete: tooling, schema, ingestion, transform, five engine layers, the
API and the UI. Phase 11 — orchestration, incremental refresh and deployment — remains.

Not yet built: qualifying and practice ingestion, sector decomposition, actual-versus-
optimal strategy scoring, and driver style fingerprints.

The reference backfill covers 2022 and 2023 — 44 races, 47,997 laps, 16.2 M telemetry
samples — and any race from 2018 onward can be pulled in on demand.


## Licence and attribution

Timing and telemetry data is retrieved through [FastF1](https://github.com/theOehrly/Fast-F1)
from the Formula 1 Live Timing service, and pre-2018 results through
[Jolpica](https://github.com/jolpica/jolpica-f1). This is an unofficial project, not
associated with Formula 1, the FIA, or any team. F1 and Formula 1 are trademarks of
Formula One Licensing BV.

Intended for research and analysis. Use of the underlying data is subject to the terms
of its providers.
