"""f1x command line interface."""

from __future__ import annotations

import typer
from rich.console import Console
from rich.table import Table

from f1x.config import ENGINE_VERSION, get_settings

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
            "\n[yellow]Start the stack with:[/] "
            "docker compose -f docker/docker-compose.yml up -d"
        )
        raise typer.Exit(1)
    console.print("\n[green]All checks passed.[/]")


db_app = typer.Typer(help="Database inspection.", no_args_is_help=True)
app.add_typer(db_app, name="db")


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


if __name__ == "__main__":
    app()
