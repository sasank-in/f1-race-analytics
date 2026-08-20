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


if __name__ == "__main__":
    app()
