# Data model

## Why TimescaleDB

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

## Source data notes

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
