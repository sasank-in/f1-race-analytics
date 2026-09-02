# F1 Race Analysis Engine — Architecture

## 1. Purpose

An advanced motorsport analytics platform. It ingests official F1 timing, telemetry and
positional data, normalises it into a time-series warehouse, and runs a layered analysis
engine that produces pace, degradation, strategy, telemetry and predictive insight —
served through a typed API into an interactive UI.

The target is *analyst-grade* output: fuel-corrected pace, tyre degradation curves,
undercut windows, corner-by-corner telemetry deltas, Monte Carlo race and championship
simulation — not lap-time line charts.

## 2. System Diagram

```
             FastF1 (Ergast / Livetiming)  ──┐
             Weather / Circuit metadata     ──┤
                                              ▼
                                 ┌────────────────────────┐
                                 │   Ingestion Layer      │  extract → validate → conform
                                 └───────────┬────────────┘
                                             ▼
                                 ┌────────────────────────┐
                                 │  Transform (Polars)    │  cleaning, derivation, QA gates
                                 └───────────┬────────────┘
                                             ▼
                          ┌──────────────────────────────────────┐
                          │ PostgreSQL 16 + TimescaleDB          │
                          │  relational: events, sessions,       │
                          │             drivers, laps, stints    │
                          │  hypertables: telemetry, positions,  │
                          │             weather, race_control    │
                          │  + continuous aggregates             │
                          └──────────────────┬───────────────────┘
                                             ▼
                          ┌──────────────────────────────────────┐
                          │        Analysis Engine (pure)        │
                          │   Pace │ Degradation │ Strategy      │
                          │   Telemetry │ Simulation │ ML        │
                          └──────────────────┬───────────────────┘
                                             ▼
                          ┌──────────────────────────────────────┐
                          │  FastAPI  (Redis cache, Pydantic)    │
                          └──────────────────┬───────────────────┘
                                             ▼
                          ┌──────────────────────────────────────┐
                          │  Next.js 15 UI (App Router, ECharts) │
                          └──────────────────────────────────────┘
```

## 3. Design Principles

1. **Raw data is immutable.** Ingested payloads land in `raw.*` and are never edited.
   Every derived value is reproducible by re-running the transform from raw.
2. **The engine is pure.** Analysis functions take DataFrames plus configuration and
   return DataFrames or dataclasses. No DB or HTTP access inside `engine/` — which makes
   every metric unit-testable against recorded fixtures.
3. **Telemetry is a time series; everything else is relational.** Hypertables only where
   row counts justify them.
4. **Everything is versioned.** Each derived row records the `engine_version` that
   produced it, so caches and materialisations invalidate correctly when a model changes.
5. **Typed end to end.** Pydantic models generate the OpenAPI schema; the UI generates its
   TypeScript client from that schema. No hand-written response types.

## 4. Repository Layout

```
f1-race/
├── docker/                     compose stack: timescaledb, redis, api, worker, web
├── db/
│   ├── migrations/             Alembic revisions
│   └── sql/                    hypertable, cagg, index & retention DDL
├── backend/
│   ├── pyproject.toml
│   └── src/f1x/
│       ├── config.py           pydantic-settings
│       ├── ingest/             fastf1 client, session loader, backfill CLI
│       ├── transform/          cleaning, derivations, QA validators
│       ├── models/             SQLAlchemy ORM + Pydantic schemas
│       ├── repo/               data access — the only place that touches SQL
│       ├── engine/
│       │   ├── pace/           fuel correction, clean-air pace, stint regression
│       │   ├── degradation/    tyre deg curves, compound life models
│       │   ├── strategy/       pit loss, undercut/overcut, optimal stops
│       │   ├── telemetry/      delta-time, corner detection, line comparison
│       │   ├── simulation/     Monte Carlo race + championship sim
│       │   ├── predictive/     quali/race outcome models, feature store
│       │   └── metrics/        composite ratings, driver/team scorecards
│       ├── api/                FastAPI routers, deps, caching, errors
│       └── jobs/               scheduled refresh + materialisation tasks
├── frontend/                   Next.js 15 + TS + Tailwind + ECharts
├── notebooks/                  exploration & model validation
└── tests/                      unit (fixtures), integration (testcontainers)
```

## 5. Data Model

### Relational

| table | grain | key content |
|---|---|---|
| `seasons` | year | championship metadata |
| `circuits` | circuit | geometry, corners, elevation, pit-lane length |
| `events` | season + round | GP name, dates, format (sprint / conventional) |
| `sessions` | event + type | FP1–3, Q, SQ, S, R; status, data availability |
| `drivers` / `teams` / `entries` | season-scoped | numbers, codes, colours, lineups |
| `results` | session + driver | classification, grid, points, status |
| `laps` | session + driver + lap | lap & sector times, compound, tyre life, position, track status, deleted flag |
| `stints` | session + driver + stint | compound, in/out laps, length, deg fit params |
| `pit_stops` | session + driver + stop | pit-in/out, stationary time, lane loss |

### Hypertables (TimescaleDB)

| table | grain | notes |
|---|---|---|
| `telemetry` | session + driver + time | speed, rpm, gear, throttle, brake, DRS, distance |
| `positions` | session + driver + time | x, y, z track position, status |
| `weather` | session + time | air/track temp, humidity, pressure, wind, rainfall |
| `race_control` | session + time | flags, SC/VSC, investigations, penalties |

Compression after 7 days, chunk interval sized to one hour of session time, and continuous
aggregates for per-lap telemetry summaries (min/max/avg speed, brake %, full-throttle %).

### Derived / materialised

`lap_metrics` (fuel-corrected, clean-air flagged), `deg_models`, `pace_rankings`,
`strategy_windows`, `driver_ratings` — each stamped with `engine_version` and `computed_at`.

## 6. Analysis Engine — Capability Map

### 6.1 Pace
- Fuel-corrected lap time (per-circuit fuel-effect coefficient, kg/lap burn model)
- Clean-air detection (gap-ahead threshold plus traffic classification)
- Representative pace: trimmed mean / quantile over valid green-flag laps
- Stint linear and non-linear regression → intercept (raw pace) + slope (degradation)
- Teammate delta normalised by session conditions
- Track-evolution modelling (session-wide grip trend removal)
- Sector and mini-sector pace decomposition
- Qualifying: run-plan reconstruction, ideal lap, purple-sector composition

### 6.2 Degradation
- Compound-specific curves (linear, exponential, piecewise cliff detection)
- Thermal vs wear degradation separation using track temp and stint length
- Cross-stint pooled regression with driver and traffic effects removed
- Predicted lap-time loss at N laps per compound → feeds the strategy layer

### 6.3 Strategy
- Pit-lane time-loss estimation per circuit (in/out-lap penalty plus stationary time)
- Undercut and overcut windows against any rival, lap by lap
- Optimal stop count and stop-lap optimisation given degradation and pit loss
- Safety-car / VSC opportunity valuation and counterfactuals ("what if SC on lap X")
- Actual vs optimal strategy scoring — where the race was won or lost
- Tyre allocation and set-usage tracking across a weekend

### 6.4 Telemetry
- Distance-resampled trace alignment between any two laps
- Cumulative delta-time: where and how much time is gained or lost
- Corner detection from curvature of positional data; per-corner minimum speed,
  entry/apex/exit speeds, braking point, throttle-application point
- Braking and traction-zone analysis; full-throttle percentage
- Racing-line comparison from x/y positions, with a line-deviation heat map
- Gear and DRS usage maps; speed-trap and top-speed profiling
- Driver style fingerprint (brake aggression, throttle modulation, coasting)

### 6.5 Simulation
- Monte Carlo race simulation: degradation model + pit loss + SC probability + traffic
- Strategy-tree evaluation across compounds and stop counts
- Championship simulation over the remaining calendar with driver/team pace priors
- Sensitivity analysis on assumptions (deg slope, SC rate, pit loss)

### 6.6 Predictive / ML
- Feature store built from historical sessions (pace, deg, track type, weather)
- Qualifying-position and race-result models (gradient boosting)
- Lap-time forecasting conditioned on stint state
- Model registry with a backtesting harness and calibration metrics

### 6.7 Composite metrics (add-ons)
- Driver rating: pace + racecraft (positions gained vs expected) + consistency + tyre management
- Team operational rating: pit-crew speed distribution, strategy-call quality
- Overtaking difficulty index per circuit; DRS effectiveness per track
- Weather-impact modelling for wet and intermediate sessions
- Anomaly detection: engine modes, sandbagging, unusual fuel loads

## 7. API Surface

```
/api/v1/seasons, /events, /sessions
/api/v1/sessions/{id}/laps, /results, /stints, /pitstops, /weather, /racecontrol
/api/v1/analysis/pace/{session_id}
/api/v1/analysis/degradation/{session_id}
/api/v1/analysis/strategy/{session_id}/undercut
/api/v1/analysis/telemetry/compare                       (two driver-laps)
/api/v1/analysis/telemetry/corners/{session_id}/{driver}/{lap}
/api/v1/simulate/race, /simulate/championship
/api/v1/predict/qualifying, /predict/race
/api/v1/ratings/drivers, /ratings/teams
```

Heavy endpoints are cached in Redis keyed by `(route, params, engine_version)` and served
from materialised tables wherever the result is precomputed.

## 8. Frontend Surface

Session explorer · pace & degradation dashboard · strategy board with undercut matrix ·
telemetry comparison (trace + delta + track map) · race simulator with adjustable
assumptions · championship projection · driver and team scorecards.

## 9. Delivery Phases

| phase | scope | exit criteria |
|---|---|---|
| **0** | Repo, tooling, Docker stack, config, CI, test harness | `docker compose up` gives Timescale + Redis; test suite green |
| **1** | Schema, migrations, hypertables, continuous aggregates | full DDL applied, seed reference data loaded |
| **2** | Ingestion: FastF1 client, session loader, backfill CLI, QA gates | one full season ingested and validated |
| **3** | Transform and derivations: lap validity, stints, pit stops, clean air | `lap_metrics` materialised for a season |
| **4** | Engine — Pace & Degradation | pace rankings and deg curves, unit-tested on fixtures |
| **5** | Engine — Strategy & pit loss | undercut matrix and optimal-stop solver |
| **6** | Engine — Telemetry & corners | delta-time, corner table, line comparison |
| **7** | Engine — Simulation | Monte Carlo race and championship |
| **8** | Engine — Predictive/ML + composite ratings | backtested models, driver ratings |
| **9** | FastAPI service: caching, OpenAPI, auth | all endpoints live, typed client generated |
| **10** | Next.js UI | full dashboard set against the live API |
| **11** | Orchestration, incremental refresh, observability, deploy | scheduled per-race updates |

Each phase is independently runnable and tested before the next begins.
