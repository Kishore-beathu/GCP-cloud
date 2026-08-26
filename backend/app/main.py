"""FastAPI application for the pharma/life-sciences trading intelligence agent."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.config import get_settings
from app.database import (
    create_all,
    dispose_engine,
    get_session_factory,
    missing_columns,
)
from app.integrations.finnhub_stream import finnhub_stream
from app.logging_config import configure_logging
from app.routers import (
    alerts,
    auth,
    backtest,
    fundamentals,
    news,
    portfolios,
    scores,
    setups,
    shortlist,
    stocks,
    system,
    ws,
)
from app.scheduler import shutdown_scheduler, start_scheduler
from app.security import auth_enabled, require_auth, require_secure_configuration
from app.services.tickers import seed_stocks

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Create tables, seed the universe, and run the scheduler for the app's lifetime."""
    settings = get_settings()
    configure_logging(settings)
    logger.info("Starting %s (%s)", settings.app_name, settings.environment)

    # Refuses to boot a public deployment that would accept anonymous writes.
    require_secure_configuration(settings)
    if auth_enabled(settings):
        logger.info("Authentication enabled: API requires a bearer token")
    else:
        logger.warning(
            "Authentication disabled (no AUTH_PASSWORD set) - suitable for local "
            "development only"
        )

    if settings.create_tables_on_startup:
        await create_all()
        async with get_session_factory()() as session:
            await seed_stocks(session)

    # create_all() adds missing tables but never alters existing ones, so a
    # database created before a column was added stays silently behind and
    # every query naming that column fails with a 500. Say so once, at startup,
    # with the command that fixes it.
    drift = await missing_columns()
    if drift:
        for table, columns in drift.items():
            logger.error(
                "Database table %r is missing column(s) %s. Requests touching it "
                "will fail. Apply migrations: `alembic stamp %s && alembic upgrade head` "
                "(stamp only if this database was never managed by Alembic).",
                table,
                ", ".join(columns),
                "3e9022270db3",
            )

    start_scheduler()
    await finnhub_stream.start()
    try:
        yield
    finally:
        await finnhub_stream.stop()
        shutdown_scheduler()
        await dispose_engine()
        logger.info("Shutdown complete")


def create_app() -> FastAPI:
    """Build the application. Exposed as a factory so tests can override settings."""
    settings = get_settings()

    app = FastAPI(
        title=settings.app_name,
        version="0.1.0",
        description=(
            "Real-time news, sentiment, and price intelligence for pharma, "
            "life-sciences, and AI equities."
        ),
        lifespan=lifespan,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(auth.router)
    # /health stays open so load balancers and uptime checks can probe it;
    # the system router guards its own admin routes individually.
    app.include_router(system.router)
    app.include_router(news.router, dependencies=[Depends(require_auth)])
    app.include_router(stocks.router, dependencies=[Depends(require_auth)])
    app.include_router(alerts.router, dependencies=[Depends(require_auth)])
    app.include_router(backtest.router, dependencies=[Depends(require_auth)])
    app.include_router(fundamentals.router, dependencies=[Depends(require_auth)])
    app.include_router(scores.router, dependencies=[Depends(require_auth)])
    app.include_router(setups.router, dependencies=[Depends(require_auth)])
    app.include_router(shortlist.router, dependencies=[Depends(require_auth)])
    app.include_router(portfolios.router, dependencies=[Depends(require_auth)])
    # The WebSocket authenticates in its handshake: browsers cannot set headers.
    app.include_router(ws.router)

    @app.exception_handler(Exception)
    async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
        """Log the traceback server-side, return a generic 500 to the caller."""
        logger.exception("Unhandled error on %s %s", request.method, request.url.path)
        return JSONResponse(status_code=500, content={"detail": "Internal server error"})

    return app


app = create_app()
