"""f1x command line interface."""

from __future__ import annotations

from typing import TYPE_CHECKING

import typer
from rich.console import Console
from rich.table import Table

from f1x.config import ENGINE_VERSION, get_settings

if TYPE_CHECKING:
    from sqlalchemy import Engine

    from f1x.transform import TransformResult

app = typer.Typer(
    name="f1x",
    help="F1 Race Analysis Engine",
    no_args_is_help=True,
    add_completion=False,
)
console = Console()


@app.command()
def version() -> None:
    """Print component versions."""
    from f1x import __version__

    console.print(f"f1x [bold cyan]{__version__}[/]  (engine {ENGINE_VERSION})")


@app.command()
def doctor() -> None:
    """Check that every dependency the stack needs is reachable."""
    settings = get_settings()
    table = Table("check", "status", "detail", title="f1x doctor")
    ok = True

    # --- PostgreSQL + TimescaleDB ---
    try:
        from sqlalchemy import create_engine, text

        engine = create_engine(str(settings.database_url), pool_pre_ping=True)
        with engine.connect() as conn:
            pg = conn.execute(text("SHOW server_version")).scalar_one()
            ts = conn.execute(
                text("SELECT extversion FROM pg_extension WHERE extname = 'timescaledb'")
            ).scalar_one_or_none()
            schemas = conn.execute(
                text(
                    "SELECT string_agg(schema_name, ', ' ORDER BY schema_name) "
                    "FROM information_schema.schemata "
                    "WHERE schema_name IN ('raw', 'core', 'mart')"
                )
            ).scalar_one_or_none()
        table.add_row("postgres", "[green]ok[/]", f"server {pg} on port {settings.db_port}")
        if ts:
            table.add_row("timescaledb", "[green]ok[/]", f"extension {ts}")
        else:
            table.add_row("timescaledb", "[red]missing[/]", "extension not installed")
            ok = False
        table.add_row("schemas", "[green]ok[/]" if schemas else "[yellow]none[/]", schemas or "-")
    except Exception as exc:  # noqa: BLE001 - doctor reports failures, never raises
        table.add_row("postgres", "[red]fail[/]", str(exc).splitlines()[0][:70])
        ok = False

    # --- Redis ---
    try:
        import redis

        client = redis.Redis.from_url(str(settings.redis_url), socket_connect_timeout=3)
        client.ping()
        info = client.info("server")
        table.add_row("redis", "[green]ok[/]", f"server {info['redis_version']}")
    except Exception as exc:  # noqa: BLE001 - doctor reports failures, never raises
        table.add_row("redis", "[red]fail[/]", str(exc).splitlines()[0][:70])
        ok = False

    # --- FastF1 ---
    try:
        import fastf1

        settings.fastf1_cache_dir.mkdir(parents=True, exist_ok=True)
        fastf1.Cache.enable_cache(str(settings.fastf1_cache_dir))
        table.add_row("fastf1", "[green]ok[/]", f"{fastf1.__version__}, cache enabled")
    except Exception as exc:  # noqa: BLE001 - doctor reports failures, never raises
        table.add_row("fastf1", "[red]fail[/]", str(exc).splitlines()[0][:70])
        ok = False

    console.print(table)
    if not ok:
        console.print(
            "\n[yellow]Start the stack with:[/] docker compose -f docker/docker-compose.yml up -d"
        )
        raise typer.Exit(1)
    console.print("\n[green]All checks passed.[/]")


db_app = typer.Typer(help="Database inspection.", no_args_is_help=True)
app.add_typer(db_app, name="db")


ingest_app = typer.Typer(help="Load and validate Formula 1 session data.", no_args_is_help=True)
app.add_typer(ingest_app, name="ingest")


def _ingest_one(year: int, round_number: int, kind: str, telemetry: bool) -> None:
    """Load and persist one session, keeping CLI commands deliberately thin."""
    from f1x.ingest import FastF1Client, SessionRequest
    from f1x.ingest.loader import SessionLoader
    from f1x.repo import create_session_factory

    settings = get_settings()
    engine, sessions = create_session_factory(settings)
    try:
        request = SessionRequest(year, round_number, kind, telemetry=telemetry)
        source = FastF1Client(settings).load(request)
        summary = SessionLoader(sessions).persist(request, source)
    finally:
        engine.dispose()

    table = Table("field", "value", title=f"{year} round {round_number} {kind} ingested")
    for field, value in (
        ("session id", summary.session_id),
        ("drivers", summary.drivers),
        ("laps", summary.laps),
        ("telemetry samples", summary.telemetry),
        ("position samples", summary.positions),
        ("weather samples", summary.weather),
        ("race-control messages", summary.race_control),
        ("timed laps", summary.quality.timed_laps),
    ):
        table.add_row(str(field), f"{value:,}" if isinstance(value, int) else str(value))
    console.print(table)
    for warning in summary.quality.warnings:
        console.print(f"[yellow]warning:[/] {warning}")


@ingest_app.command("session")
def ingest_session(
    year: int = typer.Argument(..., min=1950),
    round_number: int = typer.Argument(..., min=1),
    kind: str = typer.Argument(..., help="FP1, FP2, FP3, Q, SQ, S, or R"),
    telemetry: bool = typer.Option(True, "--telemetry/--no-telemetry"),
) -> None:
    """Ingest one fully-loaded event session."""
    try:
        _ingest_one(year, round_number, kind.upper(), telemetry)
    except (ValueError, RuntimeError) as exc:
        console.print(f"[red]ingestion failed:[/] {exc}")
        raise typer.Exit(1) from exc


@ingest_app.command("backfill")
def backfill(
    year: int = typer.Argument(..., min=1950),
    first_round: int = typer.Option(1, min=1),
    last_round: int = typer.Option(..., min=1, help="Last completed round to load"),
    kind: str = typer.Option("R", help="Session kind to ingest for each round"),
    telemetry: bool = typer.Option(True, "--telemetry/--no-telemetry"),
) -> None:
    """Backfill a contiguous range while continuing past individual failed rounds."""
    if last_round < first_round:
        raise typer.BadParameter("last_round must be greater than or equal to first_round")
    failed: list[int] = []
    for round_number in range(first_round, last_round + 1):
        try:
            _ingest_one(year, round_number, kind.upper(), telemetry)
        except (ValueError, RuntimeError) as exc:
            failed.append(round_number)
            console.print(f"[red]round {round_number} failed:[/] {exc}")
    if failed:
        console.print(f"[yellow]backfill completed with failures:[/] {', '.join(map(str, failed))}")
        raise typer.Exit(1)
    console.print(
        f"[green]backfill complete:[/] {year}, rounds {first_round}-{last_round}, {kind.upper()}"
    )


@db_app.command("status")
def db_status() -> None:
    """Show tables, hypertables, chunk intervals and compression state."""
    from sqlalchemy import create_engine, text

    engine = create_engine(str(get_settings().database_url), pool_pre_ping=True)
    with engine.connect() as conn:
        revision = conn.execute(
            text("SELECT version_num FROM core.alembic_version")
        ).scalar_one_or_none()

        counts = Table("schema", "tables", title="schema")
        for schema, n in conn.execute(
            text(
                "SELECT table_schema, count(*) FROM information_schema.tables "
                "WHERE table_schema IN ('core', 'mart') AND table_type = 'BASE TABLE' "
                "GROUP BY 1 ORDER BY 1"
            )
        ):
            counts.add_row(schema, str(n))

        ht = Table("hypertable", "chunk interval", "compressed", "chunks", title="hypertables")
        for name, interval, compressed, chunks in conn.execute(
            text(
                "SELECT h.hypertable_name, d.time_interval, h.compression_enabled, "
                "       (SELECT count(*) FROM timescaledb_information.chunks c "
                "        WHERE c.hypertable_name = h.hypertable_name) "
                "FROM timescaledb_information.hypertables h "
                "JOIN timescaledb_information.dimensions d "
                "  ON d.hypertable_name = h.hypertable_name "
                "WHERE h.hypertable_schema = 'core' ORDER BY 1"
            )
        ):
            ht.add_row(name, str(interval), "yes" if compressed else "no", str(chunks))

    console.print(f"migration revision: [cyan]{revision or 'none'}[/]")
    console.print(counts)
    console.print(ht)


@db_app.command("rowcounts")
def db_rowcounts() -> None:
    """Row counts for every core and mart table."""
    from sqlalchemy import create_engine, text

    engine = create_engine(str(get_settings().database_url), pool_pre_ping=True)
    table = Table("table", "rows", title="row counts")
    with engine.connect() as conn:
        names = [
            f"{r[0]}.{r[1]}"
            for r in conn.execute(
                text(
                    "SELECT table_schema, table_name FROM information_schema.tables "
                    "WHERE table_schema IN ('core', 'mart') AND table_type = 'BASE TABLE' "
                    "  AND table_name <> 'alembic_version' "
                    "ORDER BY 1, 2"
                )
            )
        ]
        for name in names:
            # Identifiers come from information_schema and are quoted before use,
            # so they cannot carry injected SQL.
            schema, _, tbl = name.partition(".")
            qualified = f'"{schema}"."{tbl}"'
            n = conn.execute(text(f"SELECT count(*) FROM {qualified}")).scalar_one()  # noqa: S608
            table.add_row(name, f"{n:,}")
    console.print(table)


transform_app = typer.Typer(
    help="Derive analysis-ready facts from ingested laps.", no_args_is_help=True
)
app.add_typer(transform_app, name="transform")


def _transform_one(engine: Engine, session_id: int) -> TransformResult:
    from f1x.transform.repository import transform_and_store

    return transform_and_store(engine, session_id)


@transform_app.command("session")
def transform_session_cmd(
    session_id: int = typer.Argument(..., help="core.sessions.id to transform"),
) -> None:
    """Transform one session into mart.lap_metrics, stints and pit stops."""
    from sqlalchemy import create_engine

    engine = create_engine(str(get_settings().database_url), pool_pre_ping=True)
    result = _transform_one(engine, session_id)

    table = Table("field", "value", title=f"session {session_id} transformed")
    table.add_row("representative laps", f"{result.representative_laps:,}")
    table.add_row("lap metrics", f"{len(result.lap_metrics):,}")
    table.add_row("stints", f"{len(result.stints):,}")
    table.add_row("pit stops", f"{len(result.pit_stops):,}")
    table.add_row("engine version", result.engine_version)
    console.print(table)

    if result.exclusions:
        reasons = Table("exclusion", "laps", title="why laps were excluded")
        for reason, count in sorted(result.exclusions.items(), key=lambda kv: -kv[1]):
            reasons.add_row(reason, f"{count:,}")
        console.print(reasons)


@transform_app.command("all")
def transform_all(
    season: int | None = typer.Option(None, help="Restrict to one season"),
) -> None:
    """Transform every ingested session, or every session in one season."""
    from sqlalchemy import create_engine, text

    engine = create_engine(str(get_settings().database_url), pool_pre_ping=True)
    # Two literal statements rather than one assembled string: the season filter
    # changes the shape of the query, not just a bound value.
    all_sessions = text(
        "SELECT s.id FROM core.sessions s JOIN core.events e ON e.id = s.event_id "
        "ORDER BY e.season_year, e.round, s.kind"
    )
    one_season = text(
        "SELECT s.id FROM core.sessions s JOIN core.events e ON e.id = s.event_id "
        "WHERE e.season_year = :season "
        "ORDER BY e.season_year, e.round, s.kind"
    )
    with engine.connect() as conn:
        rows = (
            conn.execute(one_season, {"season": season})
            if season
            else conn.execute(all_sessions)
        )
        ids = [r[0] for r in rows]

    if not ids:
        console.print("[yellow]No ingested sessions to transform.[/]")
        raise typer.Exit(1)

    table = Table("session", "laps", "representative", title="transform")
    total = 0
    for session_id in ids:
        result = _transform_one(engine, session_id)
        total += result.representative_laps
        table.add_row(
            str(session_id),
            f"{len(result.lap_metrics):,}",
            f"{result.representative_laps:,}",
        )
    console.print(table)
    console.print(f"[green]{len(ids)} sessions, {total:,} representative laps.[/]")


analyse_app = typer.Typer(
    help="Run the pace and degradation engine.", no_args_is_help=True
)
app.add_typer(analyse_app, name="analyse")


@analyse_app.command("session")
def analyse_session_cmd(
    session_id: int = typer.Argument(..., help="core.sessions.id to analyse"),
) -> None:
    """Fit stints, rank pace and build degradation curves for one session."""
    from sqlalchemy import create_engine

    from f1x.engine.repository import analyse_and_store

    engine = create_engine(str(get_settings().database_url), pool_pre_ping=True)
    result = analyse_and_store(engine, session_id)

    if not result.stint_fits and not result.ranking:
        console.print(
            f"[yellow]No lap metrics for session {session_id}.[/] "
            "Run `f1x transform session` first."
        )
        raise typer.Exit(1)

    pace = Table("#", "driver", "pace", "gap", "laps", "std", title="pace ranking")
    for entry in result.ranking[:10]:
        pace.add_row(
            str(entry.rank),
            entry.driver_number,
            f"{entry.pace_s:.3f}s",
            f"+{entry.gap_to_best_s:.3f}" if entry.rank > 1 else "-",
            str(entry.n_laps),
            f"{entry.std_s:.3f}",
        )
    console.print(pace)

    if result.curves:
        deg = Table("compound", "deg/lap", "spread", "stints", "longest", title="degradation")
        for curve in sorted(result.curves, key=lambda c: -c.degradation_s_per_lap):
            deg.add_row(
                curve.compound,
                f"{curve.degradation_s_per_lap:+.3f}s",
                f"{curve.degradation_iqr_s:.3f}",
                str(curve.n_stints),
                f"{curve.max_stint_laps} laps",
            )
        console.print(deg)

    console.print(
        f"[green]{result.reliable_fits} of {len(result.stint_fits)} stint fits reliable.[/]"
    )


@analyse_app.command("all")
def analyse_all(
    season: int | None = typer.Option(None, help="Restrict to one season"),
) -> None:
    """Analyse every transformed session, or every session in one season."""
    from sqlalchemy import create_engine, text

    from f1x.engine.repository import analyse_and_store

    engine = create_engine(str(get_settings().database_url), pool_pre_ping=True)
    all_sessions = text(
        "SELECT DISTINCT m.session_id FROM mart.lap_metrics m "
        "JOIN core.sessions s ON s.id = m.session_id "
        "JOIN core.events e ON e.id = s.event_id "
        "ORDER BY 1"
    )
    one_season = text(
        "SELECT DISTINCT m.session_id FROM mart.lap_metrics m "
        "JOIN core.sessions s ON s.id = m.session_id "
        "JOIN core.events e ON e.id = s.event_id "
        "WHERE e.season_year = :season ORDER BY 1"
    )
    with engine.connect() as conn:
        rows = (
            conn.execute(one_season, {"season": season})
            if season
            else conn.execute(all_sessions)
        )
        ids = [r[0] for r in rows]

    if not ids:
        console.print("[yellow]No transformed sessions to analyse.[/]")
        raise typer.Exit(1)

    table = Table("session", "stints", "reliable", "drivers", title="analysis")
    for session_id in ids:
        result = analyse_and_store(engine, session_id)
        table.add_row(
            str(session_id),
            str(len(result.stint_fits)),
            str(result.reliable_fits),
            str(len(result.ranking)),
        )
    console.print(table)
    console.print(f"[green]{len(ids)} sessions analysed.[/]")


strategy_app = typer.Typer(help="Race strategy analysis.", no_args_is_help=True)
app.add_typer(strategy_app, name="strategy")


@strategy_app.command("session")
def strategy_session(
    session_id: int = typer.Argument(..., help="core.sessions.id to analyse"),
) -> None:
    """Estimate pit loss and rank stop strategies for one session."""
    import polars as pl
    from sqlalchemy import create_engine, text

    from f1x.engine.strategy.optimiser import optimise
    from f1x.engine.strategy.pit_loss import estimate_from_laps

    engine = create_engine(str(get_settings().database_url), pool_pre_ping=True)
    with engine.connect() as conn:
        laps = pl.DataFrame(
            [
                dict(r)
                for r in conn.execute(
                    text("SELECT * FROM mart.lap_metrics WHERE session_id = :s"),
                    {"s": session_id},
                ).mappings()
            ]
        )
        stops = pl.DataFrame(
            [
                dict(r)
                for r in conn.execute(
                    text("SELECT * FROM core.pit_stops WHERE session_id = :s"),
                    {"s": session_id},
                ).mappings()
            ]
        )
        total_laps = conn.execute(
            text("SELECT total_laps FROM core.sessions WHERE id = :s"), {"s": session_id}
        ).scalar()
        degradation = conn.execute(
            text(
                "SELECT degradation_s_per_lap FROM mart.degradation_curves "
                "WHERE session_id = :s ORDER BY n_stints DESC LIMIT 1"
            ),
            {"s": session_id},
        ).scalar()

    loss = estimate_from_laps(stops, laps, session_id=session_id)
    if loss is None:
        console.print(f"[yellow]Not enough clean pit stops in session {session_id}.[/]")
        raise typer.Exit(1)

    summary = Table("field", "value", title=f"pit loss, session {session_id}")
    summary.add_row("clean stops", str(loss.n_stops))
    summary.add_row("pit-lane transit", f"{loss.pit_window_s:.1f}s")
    summary.add_row("reference lap", f"{loss.on_track_equivalent_s:.1f}s")
    summary.add_row("net cost of a stop", f"{loss.net_loss_s:.1f}s")
    summary.add_row("spread", f"{loss.spread_s:.1f}s")
    console.print(summary)

    if degradation and total_laps:
        ranked = optimise(
            total_laps=int(total_laps),
            slope_s_per_lap=float(degradation),
            net_pit_loss_s=loss.net_loss_s,
        )
        table = Table("stops", "stints", "deg cost", "pit cost", "total", title="strategies")
        for option in ranked:
            table.add_row(
                str(option.n_stops),
                "-".join(str(n) for n in option.stint_lengths),
                f"{option.degradation_cost_s:.0f}s",
                f"{option.pit_cost_s:.0f}s",
                f"{option.total_cost_s:.0f}s",
            )
        console.print(table)


telemetry_app = typer.Typer(help="Telemetry comparison.", no_args_is_help=True)
app.add_typer(telemetry_app, name="telemetry")


@telemetry_app.command("compare")
def telemetry_compare(
    session_id: int = typer.Argument(...),
    driver_a: str = typer.Argument(..., help="Reference driver number"),
    lap_a: int = typer.Argument(...),
    driver_b: str = typer.Argument(..., help="Comparison driver number"),
    lap_b: int = typer.Argument(...),
) -> None:
    """Compare two laps: delta time and corner-by-corner minimum speeds."""
    from sqlalchemy import create_engine

    from f1x.engine.telemetry.repository import compare_laps

    engine = create_engine(str(get_settings().database_url), pool_pre_ping=True)
    result = compare_laps(engine, session_id, (driver_a, lap_a), (driver_b, lap_b))
    if result is None:
        console.print(
            "[yellow]No telemetry for one of those laps.[/] "
            "Ingest the session with telemetry first."
        )
        raise typer.Exit(1)

    trace, matches = result
    console.print(
        f"lap time delta: [cyan]{trace.final_delta_s:+.3f}s[/] "
        f"({trace.comparison_driver} vs {trace.reference_driver})"
    )

    if matches:
        table = Table("corner", "apex", "ref", "cmp", "delta", title="corner minimum speeds")
        for reference, _, difference in matches:
            table.add_row(
                str(reference.index),
                f"{reference.apex_distance_m:.0f}m",
                f"{reference.min_speed_kmh:.0f}",
                f"{reference.min_speed_kmh + difference:.0f}",
                f"{difference:+.1f}",
            )
        console.print(table)


simulate_app = typer.Typer(help="Monte Carlo simulation.", no_args_is_help=True)
app.add_typer(simulate_app, name="simulate")


@simulate_app.command("race")
def simulate_race(
    session_id: int = typer.Argument(..., help="core.sessions.id to simulate"),
    iterations: int = typer.Option(2000, min=100, help="Monte Carlo iterations"),
) -> None:
    """Compare stop strategies by simulating the race many times."""
    import polars as pl
    from sqlalchemy import create_engine, text

    from f1x.engine.simulation.race import RaceConditions, compare_strategies
    from f1x.engine.strategy.optimiser import MAX_STOPS, split_evenly
    from f1x.engine.strategy.pit_loss import estimate_from_laps

    engine = create_engine(str(get_settings().database_url), pool_pre_ping=True)
    with engine.connect() as conn:
        laps = pl.DataFrame(
            [
                dict(r)
                for r in conn.execute(
                    text("SELECT * FROM mart.lap_metrics WHERE session_id = :s"),
                    {"s": session_id},
                ).mappings()
            ]
        )
        stops = pl.DataFrame(
            [
                dict(r)
                for r in conn.execute(
                    text("SELECT * FROM core.pit_stops WHERE session_id = :s"),
                    {"s": session_id},
                ).mappings()
            ]
        )
        total_laps = conn.execute(
            text("SELECT total_laps FROM core.sessions WHERE id = :s"), {"s": session_id}
        ).scalar()
        degradation = conn.execute(
            text(
                "SELECT degradation_s_per_lap FROM mart.degradation_curves "
                "WHERE session_id = :s ORDER BY n_stints DESC LIMIT 1"
            ),
            {"s": session_id},
        ).scalar()
        base_lap = conn.execute(
            text(
                "SELECT percentile_cont(0.5) WITHIN GROUP (ORDER BY lap_time_s) "
                "FROM mart.lap_metrics WHERE session_id = :s AND is_representative"
            ),
            {"s": session_id},
        ).scalar()

    loss = estimate_from_laps(stops, laps, session_id=session_id)
    if loss is None or not total_laps or degradation is None or base_lap is None:
        console.print(
            f"[yellow]Session {session_id} lacks the inputs to simulate.[/] "
            "Run `f1x transform` and `f1x analyse` first."
        )
        raise typer.Exit(1)

    conditions = RaceConditions(
        total_laps=int(total_laps),
        base_lap_s=float(base_lap),
        net_pit_loss_s=loss.net_loss_s,
        degradation_s_per_lap=float(degradation),
    )
    candidates = [split_evenly(int(total_laps), n + 1) for n in range(1, MAX_STOPS)]
    comparison = compare_strategies(
        conditions, candidates, iterations=iterations, seed=42
    )

    table = Table("stops", "stints", "median", "90% spread", "wins", title="race simulation")
    for result in sorted(comparison.results, key=lambda r: r.median_s):
        table.add_row(
            str(result.n_stops),
            "-".join(str(n) for n in result.stint_lengths),
            f"{result.median_s:.1f}s",
            f"{result.spread_s:.1f}s",
            f"{comparison.win_rates[result.n_stops]:.1%}",
        )
    console.print(table)
    console.print(
        f"safety car in {comparison.results[0].safety_car_rate:.0%} of runs; "
        + (
            "[green]call is clear.[/]"
            if comparison.is_decisive
            else "[yellow]too close to call.[/]"
        )
    )


@simulate_app.command("championship")
def simulate_championship_cmd(
    season: int = typer.Argument(..., help="Season to project"),
    races_remaining: int = typer.Option(..., min=1, help="Races left on the calendar"),
    iterations: int = typer.Option(5000, min=100),
) -> None:
    """Project title probabilities from demonstrated pace and current points."""
    from sqlalchemy import create_engine, text

    from f1x.engine.simulation.championship import DriverEntry, simulate_championship

    engine = create_engine(str(get_settings().database_url), pool_pre_ping=True)
    with engine.connect() as conn:
        rows = conn.execute(
            # Points and pace are aggregated separately then joined on the driver.
            # Joining results to rankings row-by-row multiplies each result by the
            # number of ranking rows, inflating season points by orders of magnitude.
            text(
                "WITH pace AS ("
                "  SELECT p.driver_number, avg(p.gap_to_best_s) AS gap"
                "  FROM mart.pace_rankings p"
                "  JOIN core.sessions s ON s.id = p.session_id"
                "  JOIN core.events e ON e.id = s.event_id"
                "  WHERE e.season_year = :season"
                "  GROUP BY 1"
                "), scored AS ("
                "  SELECT en.driver_number, sum(coalesce(r.points, 0)) AS points"
                "  FROM core.results r"
                "  JOIN core.sessions s ON s.id = r.session_id"
                "  JOIN core.events e ON e.id = s.event_id"
                "  JOIN core.entries en"
                "    ON en.session_id = r.session_id AND en.driver_id = r.driver_id"
                "  WHERE e.season_year = :season"
                "  GROUP BY 1"
                ") "
                "SELECT pace.driver_number, pace.gap, "
                "       coalesce(scored.points, 0) AS points "
                "FROM pace LEFT JOIN scored USING (driver_number) "
                "ORDER BY pace.gap"
            ),
            {"season": season},
        ).all()

    if not rows:
        console.print(f"[yellow]No pace rankings for {season}.[/] Run `f1x analyse` first.")
        raise typer.Exit(1)

    entries = [
        DriverEntry(
            driver_number=str(row.driver_number),
            current_points=float(row.points or 0.0),
            pace_gap_s=float(row.gap or 0.0),
        )
        for row in rows
    ]
    result = simulate_championship(
        entries, races_remaining, iterations=iterations, seed=42
    )

    table = Table("driver", "points", "pace gap", "title", "expected", title="championship")
    ordered = sorted(
        result.title_probability, key=lambda k: -result.title_probability[k]
    )
    lookup = {entry.driver_number: entry for entry in entries}
    for number in ordered[:10]:
        entry = lookup[number]
        table.add_row(
            number,
            f"{entry.current_points:.0f}",
            f"+{entry.pace_gap_s:.3f}s",
            f"{result.title_probability[number]:.1%}",
            f"{result.expected_points[number]:.0f}",
        )
    console.print(table)
    if not result.is_mathematically_decided:
        console.print(
            "[dim]Probabilities are conditional on current form; the title is not yet "
            "mathematically decided.[/]"
        )


@analyse_app.command("fuel")
def analyse_fuel(
    apply: bool = typer.Option(
        False, "--apply", help="Store fitted coefficients on core.circuits"
    ),
) -> None:
    """Fit the fuel effect per circuit across seasons.

    A single season cannot identify this: fuel load is derived from lap number, so the
    two are perfectly collinear. Pooling seasons with different race lengths at the
    same circuit breaks that.
    """
    from sqlalchemy import create_engine, text

    from f1x.engine.pace.fuel_repository import fit_all_circuits

    engine = create_engine(str(get_settings().database_url), pool_pre_ping=True)
    fits = fit_all_circuits(engine)
    if not fits:
        console.print("[yellow]No circuits with lap metrics.[/]")
        raise typer.Exit(1)

    table = Table(
        "circuit", "effect", "seasons", "spread", "laps", "r2", title="fuel effect"
    )
    fitted = [fit for fit in fits if fit.fitted]
    for fit in sorted(fits, key=lambda f: (not f.fitted, f.circuit_key)):
        table.add_row(
            fit.circuit_key,
            f"{fit.effect_s_per_kg:.4f}" if fit.fitted else "[dim]default[/]",
            str(fit.n_seasons),
            f"{fit.lap_count_spread} laps" if fit.n_seasons > 1 else "-",
            f"{fit.n_laps:,}",
            f"{fit.r_squared:.3f}" if fit.fitted else "-",
        )
    console.print(table)
    console.print(f"[green]{len(fitted)} of {len(fits)} circuits fitted.[/]")

    if not fitted:
        reasons = {fit.reason for fit in fits if fit.reason}
        for reason in sorted(reasons):
            console.print(f"[dim]  {reason}[/]")
        return

    if apply:
        with engine.begin() as conn:
            for fit in fitted:
                conn.execute(
                    text(
                        "UPDATE core.circuits SET fuel_effect_s_per_kg = :e "
                        "WHERE key = :k"
                    ),
                    {"e": fit.effect_s_per_kg, "k": fit.circuit_key},
                )
        console.print(
            f"[green]Stored {len(fitted)} coefficients.[/] "
            "Re-run `f1x transform all` to apply them."
        )
    else:
        console.print("[dim]Pass --apply to store these on core.circuits.[/]")


@analyse_app.command("quality")
def analyse_quality(
    season: int | None = typer.Option(None, help="Restrict to one season"),
) -> None:
    """Report how well the fuel correction performed, session by session.

    A residual positive trend means over-correction; negative means the burn-off is
    still showing through. Negative degradation slopes are the loud failure: tyres
    cannot get faster with age.
    """
    import polars as pl
    from sqlalchemy import create_engine, text

    from f1x.engine.pace.diagnostics import assess_correction, assess_degradation

    engine = create_engine(str(get_settings().database_url), pool_pre_ping=True)
    all_sessions = text(
        "SELECT s.id, c.key FROM core.sessions s "
        "JOIN core.events e ON e.id = s.event_id "
        "LEFT JOIN core.circuits c ON c.id = e.circuit_id ORDER BY e.season_year, e.round"
    )
    one_season = text(
        "SELECT s.id, c.key FROM core.sessions s "
        "JOIN core.events e ON e.id = s.event_id "
        "LEFT JOIN core.circuits c ON c.id = e.circuit_id "
        "WHERE e.season_year = :season ORDER BY e.round"
    )
    with engine.connect() as conn:
        rows = (
            conn.execute(one_season, {"season": season})
            if season
            else conn.execute(all_sessions)
        )
        sessions = [(r[0], r[1]) for r in rows]

        table = Table("circuit", "raw", "corrected", "removed", "verdict",
                      title="fuel correction quality")
        counts: dict[str, int] = {}
        for session_id, circuit in sessions:
            laps = pl.DataFrame(
                [
                    dict(r)
                    for r in conn.execute(
                        text("SELECT * FROM mart.lap_metrics WHERE session_id = :s"),
                        {"s": session_id},
                    ).mappings()
                ]
            )
            quality = assess_correction(
                laps, session_id=session_id, circuit_key=circuit
            )
            if quality is None:
                continue
            counts[quality.verdict] = counts.get(quality.verdict, 0) + 1
            colour = {
                "good": "green", "improved": "cyan",
                "weak": "yellow", "over-corrected": "red",
            }[quality.verdict]
            table.add_row(
                circuit or str(session_id),
                f"{quality.raw_trend:+.3f}",
                f"{quality.corrected_trend:+.3f}",
                f"{quality.improvement:.0%}",
                f"[{colour}]{quality.verdict}[/]",
            )

        curves = pl.DataFrame(
            [
                dict(r)
                for r in conn.execute(
                    text("SELECT degradation_s_per_lap FROM mart.degradation_curves")
                ).mappings()
            ]
        )

    console.print(table)
    console.print(" ".join(f"{verdict}={n}" for verdict, n in sorted(counts.items())))

    health = assess_degradation(curves)
    if health.is_healthy:
        console.print(
            f"[green]All {health.n_curves} degradation curves are physically possible.[/]"
        )
    else:
        console.print(
            f"[red]{health.n_negative} of {health.n_curves} degradation curves have a "
            f"negative slope[/] — tyres cannot get faster with age. The residual is "
            f"track evolution the linear model does not fully remove; see "
            f"transform/corrections.py."
        )


ratings_app = typer.Typer(help="Composite driver ratings.", no_args_is_help=True)
app.add_typer(ratings_app, name="ratings")


@ratings_app.command("drivers")
def ratings_drivers(
    season: int = typer.Argument(..., help="Season to rate"),
) -> None:
    """Rate drivers on pace, racecraft, consistency and tyre management.

    Scores are relative to the drivers being compared, so they say who was better
    within a season and nothing about absolute standard across seasons.
    """
    import polars as pl
    from sqlalchemy import create_engine, text

    from f1x.engine.metrics.ratings import build_ratings

    engine = create_engine(str(get_settings().database_url), pool_pre_ping=True)

    def frame(sql: str) -> pl.DataFrame:
        with engine.connect() as conn:
            rows = [dict(r) for r in conn.execute(text(sql), {"season": season}).mappings()]
        return pl.DataFrame(rows) if rows else pl.DataFrame()

    pace = frame(
        "SELECT p.* FROM mart.pace_rankings p "
        "JOIN core.sessions s ON s.id = p.session_id "
        "JOIN core.events e ON e.id = s.event_id "
        "WHERE e.season_year = :season"
    )
    results = frame(
        "SELECT en.driver_number, r.grid_position, r.position "
        "FROM core.results r "
        "JOIN core.sessions s ON s.id = r.session_id "
        "JOIN core.events e ON e.id = s.event_id "
        "JOIN core.entries en "
        "  ON en.session_id = r.session_id AND en.driver_id = r.driver_id "
        "WHERE e.season_year = :season"
    )
    stints = frame(
        "SELECT f.* FROM mart.stint_fits f "
        "JOIN core.sessions s ON s.id = f.session_id "
        "JOIN core.events e ON e.id = s.event_id "
        "WHERE e.season_year = :season"
    )

    ratings = build_ratings(pace, results, stints)
    if not ratings:
        console.print(
            f"[yellow]No ratings for {season}.[/] Run `f1x analyse all` first."
        )
        raise typer.Exit(1)

    table = Table(
        "#", "driver", "overall", "pace", "racecraft", "consistency", "tyres",
        "strongest", title=f"{season} driver ratings",
    )
    for rating in ratings[:15]:
        table.add_row(
            str(rating.rank),
            rating.driver_number,
            f"{rating.overall:.1f}",
            f"{rating.pace:.0f}",
            f"{rating.racecraft:.0f}",
            f"{rating.consistency:.0f}",
            f"{rating.tyre_management:.0f}",
            rating.strongest,
        )
    console.print(table)
    console.print(
        "[dim]Scores are relative to this season's field, not an absolute scale.[/]"
    )


if __name__ == "__main__":
    app()
