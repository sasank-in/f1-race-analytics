"""FastAPI application.

The API is a thin projection of the engine. It reads marts, calls pure analysis
functions, and adds nothing analytical of its own — so a number served here is the same
number the CLI prints, and a bug can only be in one place.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from f1x.api.deps import get_cache, get_engine
from f1x.api.routers import analysis, reference, strategy, telemetry
from f1x.config import ENGINE_VERSION, get_settings

logger = logging.getLogger(__name__)

DESCRIPTION = """
Advanced Formula 1 analytics.

Every analysis response carries the `engine_version` that produced it. Cached results
are keyed on that version, so changing a model invalidates its cached values rather
than mixing two definitions in one chart.

**On estimates.** Degradation slopes, fuel-corrected lap times and pit loss are modelled
from observed timing, not measured from team telemetry. Fields that carry an estimate
say so in their description. In particular the fuel coefficient is a published
0.030 s/kg prior, not a value fitted from this dataset — it cannot be fitted from race
data, because fuel load is derived from lap number and carries no independent information.
"""


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    """Fail fast on a database that is not reachable at startup."""
    engine = get_engine()
    try:
        from sqlalchemy import text

        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        logger.info("database reachable, engine version %s", ENGINE_VERSION)
    except Exception:
        logger.exception("database unreachable at startup")
        raise
    yield
    engine.dispose()


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title="F1 Race Analysis Engine",
        description=DESCRIPTION,
        version=ENGINE_VERSION,
        lifespan=lifespan,
        docs_url="/docs",
        openapi_url="/openapi.json",
    )

    # The UI is served from a different origin in development. Locked to localhost
    # rather than "*" so a browser on another site cannot read this API.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:3000", "http://127.0.0.1:3000"],
        allow_credentials=True,
        allow_methods=["GET"],
        allow_headers=["*"],
    )

    api = "/api/v1"
    app.include_router(reference.router, prefix=api)
    app.include_router(analysis.router, prefix=api)
    app.include_router(strategy.router, prefix=api)
    app.include_router(telemetry.router, prefix=api)

    @app.get("/health", tags=["ops"])
    def health() -> dict[str, Any]:
        """Liveness and dependency check, for a load balancer or `f1x doctor`."""
        from sqlalchemy import text

        status: dict[str, Any] = {
            "engine_version": ENGINE_VERSION,
            "env": settings.env,
        }
        try:
            with get_engine().connect() as conn:
                status["sessions"] = conn.execute(
                    text("SELECT count(*) FROM core.sessions")
                ).scalar_one()
            status["database"] = "ok"
        except Exception as exc:  # noqa: BLE001 - health reports, never raises
            status["database"] = f"fail: {str(exc).splitlines()[0][:60]}"

        # Probe the cache for real. A miss is not a failure -- the API degrades to
        # uncached rather than broken -- so report reachability, not hit or miss.
        cache = get_cache()
        cache.set("__probe__", {"ok": True}, ttl=60)
        status["cache"] = "ok" if cache.get("__probe__") is not None else "unavailable"
        return status

    return app


app = create_app()
