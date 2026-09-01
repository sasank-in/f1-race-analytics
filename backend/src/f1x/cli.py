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


if __name__ == "__main__":
    app()
