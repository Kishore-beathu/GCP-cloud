"""Health, scheduler status, and manual ingestion triggers."""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.database import get_db, get_session_factory
from app.integrations.alpha_vantage import backfill_daily, update_quotes
from app.integrations.finnhub import ingest_finnhub_news
from app.integrations.finnhub_stream import finnhub_stream
from app.integrations.sec import ingest_sec_filings
from app.schemas import HealthResponse
from app.security import require_auth
from app.services.rescore import rescore_articles, stale_count
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


@router.get("/jobs/status", summary="Scheduled job and live-stream status", dependencies=[Depends(require_auth)])
async def jobs_status() -> dict:
    from app.scheduler import job_status

    return {**job_status(), "price_stream": finnhub_stream.status()}


@router.post("/admin/seed", summary="Seed the stock universe", dependencies=[Depends(require_auth)])
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


@router.post("/admin/ingest/sec", status_code=202, summary="Trigger SEC ingestion", dependencies=[Depends(require_auth)])
async def trigger_sec_ingest(
    background: BackgroundTasks,
    ticker: list[str] | None = Query(
        default=None, description="Repeat to limit the run to specific symbols"
    ),
) -> dict:
    """Kick off an SEC EDGAR pull. Returns immediately; the work runs in the background."""
    background.add_task(_run_sec_ingest, ticker)
    return {"status": "accepted", "tickers": ticker or "all active"}


async def _run_finnhub_ingest(tickers: list[str] | None) -> None:
    async with get_session_factory()() as session:
        try:
            await ingest_finnhub_news(session, tickers)
        except Exception:
            logger.exception("Manual Finnhub ingest failed")


@router.post("/admin/ingest/finnhub", status_code=202, summary="Trigger Finnhub news ingestion", dependencies=[Depends(require_auth)])
async def trigger_finnhub_ingest(
    background: BackgroundTasks,
    ticker: list[str] | None = Query(
        default=None, description="Repeat to limit the run to specific symbols"
    ),
) -> dict:
    """Kick off a Finnhub news pull. No-op (with a log line) when the key is missing."""
    background.add_task(_run_finnhub_ingest, ticker)
    return {"status": "accepted", "tickers": ticker or "all active"}


async def _run_quote_update(tickers: list[str] | None) -> None:
    async with get_session_factory()() as session:
        try:
            await update_quotes(session, tickers)
        except Exception:
            logger.exception("Manual quote update failed")


@router.post("/admin/ingest/prices", status_code=202, summary="Trigger a quote refresh", dependencies=[Depends(require_auth)])
async def trigger_quote_update(
    background: BackgroundTasks,
    ticker: list[str] | None = Query(
        default=None, description="Repeat to limit the run to specific symbols"
    ),
) -> dict:
    """Refresh latest quotes from Alpha Vantage. No-op when the key is missing."""
    background.add_task(_run_quote_update, ticker)
    return {"status": "accepted", "tickers": ticker or "all active"}


@router.post("/admin/backfill/prices", summary="Backfill daily price history for one ticker", dependencies=[Depends(require_auth)])
async def trigger_price_backfill(
    ticker: str = Query(description="Symbol to backfill, e.g. MRNA"),
    outputsize: str = Query(
        default="compact",
        pattern="^(compact|full)$",
        description="compact = ~100 days, full = 20+ years",
    ),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Load daily history synchronously (one API call) so backtests have data.

    Runs inline rather than in the background because the caller usually wants
    to know the row counts — and whether the API key is configured — right away.
    """
    try:
        result = await backfill_daily(db, ticker, outputsize)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"ticker": ticker.upper(), **result}


@router.get(
    "/admin/diagnose/sources",
    summary="Probe every upstream data source and report what it said",
    dependencies=[Depends(require_auth)],
)
async def diagnose_sources() -> dict:
    """Make one live call per vendor so an empty dashboard has an explanation.

    Ingestion degrades quietly by design, which hides *which* source failed.
    This reports each vendor's own wording. No key is echoed back.
    """
    from app.services.diagnostics import probe_sources

    return await probe_sources(get_settings())


@router.get(
    "/admin/sentiment/status",
    summary="How many stored scores are stale",
    dependencies=[Depends(require_auth)],
)
async def sentiment_status(db: AsyncSession = Depends(get_db)) -> dict:
    """Compare stored scores against the current model version."""
    return await stale_count(db)


@router.post(
    "/admin/sentiment/rescore",
    summary="Re-score stored articles with the current model",
    dependencies=[Depends(require_auth)],
)
async def rescore(
    limit: int = Query(default=1000, ge=1, le=20000),
    only_stale: bool = Query(
        default=True, description="False re-scores everything, not just older versions"
    ),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Apply lexicon improvements to news already in the database.

    Runs inline so the caller sees the counts. Alerts are deliberately not
    re-fired: they already fired, or did not, when the news arrived.
    """
    report = await rescore_articles(db, limit=limit, only_stale=only_stale)
    return report.as_dict()
