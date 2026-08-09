"""Health, scheduler status, and manual ingestion triggers."""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from fastapi import APIRouter, BackgroundTasks, Depends, Query
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.database import get_db, get_session_factory
from app.integrations.sec import ingest_sec_filings
from app.schemas import HealthResponse
from app.services.tickers import seed_stocks

logger = logging.getLogger(__name__)
router = APIRouter(tags=["system"])


@router.get("/health", response_model=HealthResponse, summary="Liveness and dependency check")
async def health(db: AsyncSession = Depends(get_db)) -> HealthResponse:
    settings = get_settings()
    try:
        await db.execute(text("SELECT 1"))
        database = "ok"
    except Exception as exc:
        logger.error("Health check database probe failed: %s", exc)
        database = "unavailable"

    return HealthResponse(
        status="ok" if database == "ok" else "degraded",
        timestamp=datetime.now(timezone.utc),
        environment=settings.environment,
        database=database,
        sentiment_backend=settings.sentiment_backend,
    )


@router.get("/jobs/status", summary="Scheduled job status")
async def jobs_status() -> dict:
    from app.scheduler import job_status

    return job_status()


@router.post("/admin/seed", summary="Seed the stock universe")
async def seed(db: AsyncSession = Depends(get_db)) -> dict:
    added = await seed_stocks(db)
    return {"stocks_added": added}


async def _run_sec_ingest(tickers: list[str] | None) -> None:
    """Run one SEC ingest on its own session, off the request lifecycle."""
    async with get_session_factory()() as session:
        try:
            await ingest_sec_filings(session, tickers)
        except Exception:
            logger.exception("Manual SEC ingest failed")


@router.post("/admin/ingest/sec", status_code=202, summary="Trigger SEC ingestion")
async def trigger_sec_ingest(
    background: BackgroundTasks,
    ticker: list[str] | None = Query(
        default=None, description="Repeat to limit the run to specific symbols"
    ),
) -> dict:
    """Kick off an SEC EDGAR pull. Returns immediately; the work runs in the background."""
    background.add_task(_run_sec_ingest, ticker)
    return {"status": "accepted", "tickers": ticker or "all active"}
