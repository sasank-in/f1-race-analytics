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

On Windows, `start.bat` brings up all four pieces in order and opens the UI:

```
start.bat            database, cache, API and UI
start.bat --prod     the same, with the optimised UI build
start.bat --stop     stop the API and UI, leave containers running
start.bat --down     stop everything
```

It waits for each service to actually answer rather than sleeping a fixed time, and
frees ports 8000 and 3000 first — a stale Next.js server serves an old build whose CSS
hash no longer exists, which renders the page completely unstyled.

To run the pieces by hand:

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

### Ingestion

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

### Transform

Ingestion stores what the feed reported. The transform derives what it means: which laps
count, what they are worth once fuel is accounted for, and what the stint and pit-stop
structure was.

```bash
.venv/Scripts/python.exe -m f1x.cli transform session 66   # one session
.venv/Scripts/python.exe -m f1x.cli transform all --season 2023
```

Output lands in `mart.lap_metrics`, stamped with `engine_version`, alongside derived
`core.stints` and `core.pit_stops`. Every excluded lap records *why* it was excluded, so
a surprising pace number can always be traced back to its sample.

Across 2022 and 2023 — 44 races, 47,997 laps, 16.2 M telemetry samples — 80% of laps
survive filtering.
The remainder are attributed: 11% flagged inaccurate by the source, 3% run under yellow,
2.6% outliers, 1.8% untimed, 1.6% deleted by the stewards.

Three checks worth knowing, because they validate the model rather than the plumbing:

- Lap time against lap number correlates **-0.615** on average before fuel correction and
  **0.196** in absolute terms after, with 15 of 22 races landing inside ±0.2. The
  correction removes the burn-off trend it is meant to remove.
- Mean stint length orders **SOFT 11.3 < MEDIUM 17.1 < HARD 25.4** laps. Nothing in the
  code enforces that ordering; it falls out of the derivation, which is the useful signal.
- Fitted degradation across 98 compound curves orders **INTERMEDIATE 0.162 > SOFT 0.074
  > MEDIUM 0.055 > HARD 0.048** s/lap, again unenforced, and peaks at 0.175 s/lap —
  inside the [0, 0.22] band published by Kolbe et al.

The two races that over-correct do so because the fuel coefficient is a uniform
0.030 s/kg. See **Fitting the fuel effect** below for why fitting it per circuit turns
out not to be possible from single-season data.

### Analysis

```bash
.venv/Scripts/python.exe -m f1x.cli analyse session 66     # one session
.venv/Scripts/python.exe -m f1x.cli analyse all --season 2023
```

Fits every stint, ranks driver pace, and pools degradation by compound. Output lands in
`mart.stint_fits`, `mart.pace_rankings` and `mart.degradation_curves`, each stamped with
`engine_version`.

**Stint regression** is the core idea. A stint's lap times mix two effects: the car's
underlying pace, and grip lost as the tyres age. Fitting a line through the stint splits
them — the intercept is pace on a fresh set, the slope is the cost of each lap of tyre
age. "Who was quickest?" and "whose tyres lasted?" then have separate answers, which a
stint average cannot give.

**Pace ranking** uses the 20th percentile of clean fuel-corrected laps, not the fastest
lap and not the mean. The fastest lap rewards whoever got the best single opportunity;
the mean rewards whoever avoided traffic. A low quantile describes what the car could do
when pushed, without one exceptional lap defining the rating.

Over 2023 the engine ranks Verstappen fastest in **14 of 22 races**, Pérez in 3, Norris
in 2 — reconstructed from lap times alone, with no access to finishing order.

**Degradation** pools reliable stint fits by compound, reporting the median slope and its
interquartile spread. Median because one damaged car should not define a compound; spread
because a strategy model given only a centre will present a pit window as a single lap.

### Strategy

```bash
.venv/Scripts/python.exe -m f1x.cli strategy session 66
```

**Pit loss** is the number every strategy call rests on, and it is not the pit-lane
transit time. Measured at 2023 Bahrain: transit is 24.8 s, but a normal lap runs 95 s
while the in-lap runs 101 s and the out-lap 118 s. The real cost is what those two laps
add beyond two normal ones — **25.1 s**. Estimated from a low quantile, since a mean
would fold in botched stops and stops made under a safety car.

**The optimiser** trades pit loss against degradation. Degradation cost is quadratic in
stint length, so splitting a long stint always pays something; the question is whether
it pays more than the stop costs. One regulation is modelled explicitly — a dry race
requires two compounds, so one stop is mandatory. Without that floor the optimiser
returns zero stops at low-degradation circuits, which is internally consistent and
against the rules.

At Bahrain it ranks **2 stops (19-19-19) at 131 s** ahead of 3 stops at 135 s — the
strategy teams actually ran, with the runner-up close enough to show the call was
marginal rather than obvious. At Jeddah and Baku it returns **1 stop**, also matching.

Degradation is clamped at zero before costing. At low-degradation circuits the uniform
fuel coefficient over-corrects hard enough to fit a *negative* slope — tyres apparently
getting faster with age. Left unclamped that makes long stints look beneficial and
drives the optimiser to a one-stop for entirely the wrong reason. The curve is still
reported with `is_physical = False`, so the over-correction stays visible rather than
being silently absorbed.

**Undercut windows** scan every lap for a driver within three seconds of the car ahead
and ask whether fresh tyres would gain more than the gap before the rival responds.
Verdicts are `undercut`, `hold`, or `marginal`; anything inside half a second either way
is reported as marginal rather than decided.

### Telemetry

```bash
.venv/Scripts/python.exe -m f1x.cli telemetry compare 66 1 20 11 20
```

Requires a session ingested *with* telemetry (the default; `--no-telemetry` skips it).

**Alignment** is the step everything else depends on. Telemetry is sampled in time, but
a lap comparison has to happen in distance — two drivers at the same moment are at
different points on the track. Both laps are resampled onto a one-metre distance grid,
with distance integrated from speed rather than read from the feed.

**Delta time** then answers *where* a lap was won, not just by how much. A step down
under braking is a later brake point; a rise on the straight after a corner means the
time was actually won in the corner before.

**Corner detection** works from the shape of the speed trace rather than a hardcoded
circuit map: every corner is a local minimum in smoothed speed. That means the engine
works on any circuit, including ones added after it was written, and adapts to layout
changes without a data update. Per corner it reports minimum speed, entry and exit
speeds, braking point and throttle-application point.

The sharpest check on all of this is lap distance, integrated from speed alone with no
circuit data involved. Across eight circuits it lands within **0.9–2.2 %** of the true
length — Monaco 3,263 m against 3,337, Spa 6,918 against 7,004 — and the error is
consistently *negative*, the signature of trapezoidal integration under-counting during
acceleration rather than random noise.

### Simulation

```bash
.venv/Scripts/python.exe -m f1x.cli simulate race 138
.venv/Scripts/python.exe -m f1x.cli simulate championship 2023 --races-remaining 12
```

A strategy comparison that returns one number is misleading. "Two stops is four seconds
faster" sounds decisive until a safety car falls in the wrong place. The simulation runs
a race thousands of times with the uncertain quantities resampled — lap-time noise,
safety-car timing, pit variation — and reports how often each strategy actually wins.

All strategies share one random seed, so they face the same sampled races. Without that,
differences between strategies would be confounded with differences in the luck each one
happened to draw.

At Bahrain the 2-stop wins **67%** against the 3-stop's 32% — decisive, but with the
alternative live enough to be worth holding. At Jeddah the 1-stop wins outright. Both
match what teams ran.

**How the safety car is modelled** is the load-bearing decision. Slowing the neutralised
laps by 40% — the physically obvious approach — produced a **170-second spread** that
swamped the ~4 s separating strategies and made every race read as a coin toss. But a
safety car slows *every* car equally, so it barely moves the relative standing this
simulation compares. What it genuinely changes is the cost of pitting, since a stop made
under neutralisation loses far less track position. Modelling only that asymmetry cut the
spread to 15–21 s and let real differences show.

**Championship projection** samples the remaining calendar from demonstrated pace. The
`PACE_SENSITIVITY` constant is fitted, not chosen: 2023's fastest car won 19 of 22 races
(86%), and feeding this engine's own measured pace gaps through the sampler at 5.0
reproduces 84%.

Run against 2023 it recovers **Verstappen 530, Pérez 260, Hamilton 217** against actual
finals of 575, 285, 234 — the championship order reconstructed from lap times alone.

Title probabilities saturate quickly, and that is a property of the championship rather
than a flaw: a driver winning 84% of races does not lose a 12-race points lead. So
`is_mathematically_decided` reports whether the lead is genuinely uncatchable, which is a
different claim from "the model never saw them lose".

### Prediction and ratings

```bash
.venv/Scripts/python.exe -m f1x.cli ratings drivers 2023
```

**The feature store** obeys one rule: every feature must have been knowable *before*
the race it predicts. All aggregates are shifted one race back, and a driver's first
races are dropped rather than filled with a season average — filling would leak the
future into the past. `leakage_check` flags any feature correlating above 0.98 with the
target; it fired twice during development, both times on degenerate test data.

**The outcome model** is reported against the baseline it has to beat: predicting that
everyone finishes where they started. Grid position alone explains most of the variance
in a modern race, so a model that cannot beat it has learned nothing.

That check needed strengthening. On pure noise the ridge fit scored a **16.5 % lower
MAE** than grid position — predicting the middle of the field beats predicting the
extremes when outcomes are uniform — while its rank correlation was **−0.12**, ordering
the field backwards. Mean error alone cannot reject a useless model, so the comparison
now requires better rank correlation too.

**Driver ratings** score pace, racecraft, consistency and tyre management, each
normalised across the field being compared. Tyre management is measured within compound
and session, since an absolute slope mixes circuits that are not comparable, and a
driver with no valid stint fit is scored at the midpoint rather than the bottom —
missing data is missing, not bad.

On 2023: **Verstappen 81.0** led by pace 100, **Pérez 80.8** by racecraft 100,
**Hamilton 62.6** by consistency 93 — the right top three, each with a distinct profile
rather than one metric driving everything.

### API

```bash
.venv/Scripts/python.exe -m f1x.cli api serve        # http://127.0.0.1:8000/docs
.venv/Scripts/python.exe -m f1x.cli api schema       # export OpenAPI
.venv/Scripts/python.exe -m f1x.cli api cache-clear  # drop cached responses
```

Twelve endpoints over the marts: seasons, events and sessions for navigation; laps, pace
and degradation for analysis; strategy, simulation, telemetry comparison and driver
ratings for the engine's output.

The API adds nothing analytical of its own. It reads marts and calls the same pure
functions the CLI does, so a number served here is the number the CLI prints and a bug
can only be in one place.

Three things it does enforce:

**Provenance travels with every response.** Each analysis result carries the
`engine_version` that produced it, and fields holding an estimate say so in their
OpenAPI description — `fuel_corrected_s` states that it uses a published coefficient
rather than measured telemetry. A number that arrives without its caveats gets treated
as fact.

**Cache keys include the engine version.** Changing a model invalidates its cached
responses rather than serving values computed under the old definition — the same
discipline the mart tables use, applied at the edge. Redis being unreachable makes the
API slower, never broken: every cache operation degrades to a miss.

**Failures explain what to do.** A session that has not been through the engine returns
404 with "Run `f1x transform` and `f1x analyse` first", not an empty list that looks like
a session with no laps.

The frontend's TypeScript client is generated from the schema, so a renamed field
surfaces as a compile error rather than an undefined value at runtime:

```bash
npx openapi-typescript openapi.json -o frontend/src/api/schema.ts
```

### UI

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

### Prior art

Two findings from the literature bear directly on this engine, and one of them
independently confirms a bug it hit.

Kolbe et al., [*Pitwall: Faithful Natural-Language Race-Strategy Briefings from a
Calibrated Real-Time Monte Carlo Engine*](https://arxiv.org/abs/2607.06495), describes
the exact failure mode this project ran into:

> raw fitted slopes conflate tyre wear with fuel burn-off and track evolution; because
> fuel effect and track grip improve lap time as the race progresses, the blended slope
> is frequently negative — a model in which tyres improve forever, which a plan
> optimizer will exploit with impossible 50-lap soft stints.

That is precisely what happened here: negative degradation curves at Jeddah, Melbourne
and Baku, and an optimiser that read them as a reason to run long. Reaching the same
diagnosis independently is reassuring; their fix is stronger than the first one tried
here. They clamp the tyre-only component to **[0, 0.22] s/lap**, a band fitted across
8,278 stints and 191,000 laps, and this engine now uses that bound in place of the
1.5 s/lap guess it started with. Measured degradation across 2023 tops out at 0.133
s/lap, comfortably inside it.

They also fit **on green laps only, excluding in-laps and out-laps**, which matches the
validity rules in `transform/validity.py` — arrived at separately, and good to see
corroborated.

Ohlsson et al., [*A State-Space Approach to Modeling Tire Degradation in Formula
1*](https://arxiv.org/abs/2512.00640), takes the Bayesian route: lap time as a function
of fuel mass and *latent* tyre pace, with pit stops as state resets. That treats
degradation as an unobserved state to be inferred rather than a slope to be fitted, and
is the natural successor to the linear model used here.

### Fitting the fuel effect

The plan was to replace the assumed 0.030 s/kg with a coefficient fitted per circuit.
That turns out to be impossible from one season, and the reason is worth recording.

`fuel_load_kg` is derived from lap number by a linear burn assumption, so within a race
it is perfectly collinear with lap number — measured correlation exactly **-1.0**. A
regression on it cannot isolate mass; it absorbs everything that trends through a race,
including track evolution and rubber build-in, and returns roughly **1.0 s/kg**, about
thirty times the physical value.

This is an identification problem, not a numerical one. `engine/pace/fuel_model.py`
therefore fits the coefficient, rejects it against physical bounds, and falls back to the
default — a rule of thumb applied honestly beats a fitted number that is confidently
wrong on every lap.

**The cross-season fit** in `engine/pace/fuel_fit.py` resolves it. A circuit run over 57
laps one year and 53 the next carries *different fuel loads at the same lap number*, and
that decoupling is what makes mass identifiable. Season and per-stint baselines are
absorbed by dummy columns so the fuel term cannot soak them up instead.

```bash
.venv/Scripts/python.exe -m f1x.cli analyse fuel            # inspect
.venv/Scripts/python.exe -m f1x.cli analyse fuel --apply    # store on core.circuits
```

The estimator refuses rather than guesses. Single-season data, and two seasons of
identical race length, both return the published default with the reason attached —
equal distances reproduce the original collinearity exactly. A fit outside physical
bounds is discarded, never applied.

Fitted coefficients land on `core.circuits.fuel_effect_s_per_kg` and the transform picks
them up automatically, falling back to the default for circuits without enough history.

**A pooled regression was also tried and also fails**, and the way it fails is worth
recording because it looks like a success. Pooling all 44 races with stint fixed effects
and a quadratic tyre-age term yields beta_fuel = **+0.049 s/kg** over 36,642 laps at
r-squared 0.996 — squarely in the published 0.025–0.040 band.

Refitting by compound breaks it: **SOFT −0.041, MEDIUM +0.011, HARD +0.072 s/kg**. An
eleven-hundredth swing including a physically impossible negative is not a constant being
measured. The cause is that stint fixed effects leave only *within*-stint variation, and
the within-stint correlation between fuel load and tyre age is **−1.0000 across 100 % of
1,816 stints**. The two regressors are the same variable inside a stint, so the solver
partitions their shared effect arbitrarily and the partition moves with the subset.

A quadratic tyre-age term was tested separately and changes nothing — 351 negative stint
slopes with or without it — because the warm-up cutoff in `stint_model.py` already
removes the curvature it would model.

The conclusion is not that the regression was specified wrongly. It is that race data
contains no variation capable of identifying this coefficient, and the honest response is
to use the published default and say so.

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
