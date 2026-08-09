"""FastAPI application for the pharma/life-sciences trading intelligence agent."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.config import get_settings
from app.database import create_all, dispose_engine, get_session_factory
from app.logging_config import configure_logging
from app.routers import alerts, backtest, news, stocks, system, ws
from app.scheduler import shutdown_scheduler, start_scheduler
from app.services.tickers import seed_stocks

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Create tables, seed the universe, and run the scheduler for the app's lifetime."""
    settings = get_settings()
    configure_logging(settings)
    logger.info("Starting %s (%s)", settings.app_name, settings.environment)

    if settings.create_tables_on_startup:
        await create_all()
        async with get_session_factory()() as session:
            await seed_stocks(session)

    start_scheduler()
    try:
        yield
    finally:
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

    app.include_router(system.router)
    app.include_router(news.router)
    app.include_router(stocks.router)
    app.include_router(alerts.router)
    app.include_router(backtest.router)
    app.include_router(ws.router)

    @app.exception_handler(Exception)
    async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
        """Log the traceback server-side, return a generic 500 to the caller."""
        logger.exception("Unhandled error on %s %s", request.method, request.url.path)
        return JSONResponse(status_code=500, content={"detail": "Internal server error"})

    return app


app = create_app()
