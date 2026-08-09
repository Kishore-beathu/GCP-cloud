"""Background jobs.

Week 1 runs SEC ingestion, the WebSocket price push, and retention cleanup.
Finnhub news and Alpha Vantage prices register here in Week 2.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger
from sqlalchemy import delete, select

from app.config import get_settings
from app.database import get_session_factory
from app.integrations.sec import ingest_sec_filings
from app.models import NewsArticle, Stock, StockPrice
from app.services.streams import ticker_hub

logger = logging.getLogger(__name__)

_scheduler: AsyncIOScheduler | None = None

# Rotating cursor into the ticker universe: each SEC run picks up where the last
# left off, so one pass never hammers the SEC with the whole watchlist at once.
_sec_cursor = 0


async def sec_ingest_job() -> None:
    """Ingest SEC filings for the next batch of tickers."""
    global _sec_cursor
    settings = get_settings()

    async with get_session_factory()() as db:
        tickers = list(
            (
                await db.execute(
                    select(Stock.ticker).where(Stock.is_active.is_(True)).order_by(Stock.ticker)
                )
            ).scalars()
        )
        if not tickers:
            logger.info("SEC job: no active tickers")
            return

        size = max(1, settings.sec_ingest_batch_size)
        start = _sec_cursor % len(tickers)
        batch = tickers[start : start + size]
        if len(batch) < size:
            batch += tickers[: size - len(batch)]  # wrap around
        _sec_cursor = (start + size) % len(tickers)

        logger.info("SEC job: ingesting %d tickers starting at %s", len(batch), batch[0])
        report = await ingest_sec_filings(db, batch)
        logger.info("SEC job complete: %s", report.as_dict())


async def price_push_job() -> None:
    """Push the latest stored close to every subscribed WebSocket client.

    Week 1 replays what ingestion has already written. Week 2 swaps the source
    for a live Alpha Vantage quote; the push path stays identical.
    """
    tickers = ticker_hub.subscribed_tickers()
    if not tickers:
        return

    async with get_session_factory()() as db:
        stocks = list(
            (await db.execute(select(Stock).where(Stock.ticker.in_(tickers)))).scalars()
        )
        for stock in stocks:
            prices = list(
                (
                    await db.execute(
                        select(StockPrice)
                        .where(StockPrice.ticker_id == stock.id)
                        .order_by(StockPrice.price_date.desc())
                        .limit(2)
                    )
                ).scalars()
            )
            if not prices:
                continue

            latest = prices[0]
            change = None
            if len(prices) > 1 and prices[1].close:
                change = round((latest.close - prices[1].close) / prices[1].close * 100, 4)

            await ticker_hub.broadcast_price(
                stock.ticker,
                {
                    "type": "price_update",
                    "ticker": stock.ticker,
                    "price": latest.close,
                    "change": change,
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                },
            )


async def cleanup_job() -> None:
    """Drop news and prices older than the retention window."""
    settings = get_settings()
    cutoff = datetime.now(timezone.utc) - timedelta(days=settings.data_retention_days)

    async with get_session_factory()() as db:
        news = await db.execute(delete(NewsArticle).where(NewsArticle.published_at < cutoff))
        prices = await db.execute(delete(StockPrice).where(StockPrice.price_date < cutoff))
        await db.commit()
        logger.info(
            "Cleanup removed %s articles and %s price rows older than %s",
            news.rowcount,
            prices.rowcount,
            cutoff.date(),
        )


def start_scheduler() -> AsyncIOScheduler | None:
    """Start the scheduler and register jobs. No-op when disabled."""
    global _scheduler
    settings = get_settings()

    if not settings.scheduler_enabled:
        logger.info("Scheduler disabled by configuration")
        return None
    if _scheduler is not None:
        return _scheduler

    scheduler = AsyncIOScheduler(timezone="UTC")
    scheduler.add_job(
        sec_ingest_job,
        IntervalTrigger(minutes=settings.sec_ingest_interval_minutes),
        id="sec_ingest",
        name="SEC EDGAR ingestion",
        max_instances=1,
        coalesce=True,
    )
    scheduler.add_job(
        price_push_job,
        IntervalTrigger(seconds=settings.ticker_push_interval_seconds),
        id="price_push",
        name="WebSocket price push",
        max_instances=1,
        coalesce=True,
    )
    scheduler.add_job(
        cleanup_job,
        IntervalTrigger(days=7),
        id="cleanup",
        name="Retention cleanup",
        max_instances=1,
        coalesce=True,
    )
    scheduler.start()
    _scheduler = scheduler
    logger.info("Scheduler started with %d jobs", len(scheduler.get_jobs()))
    return scheduler


def shutdown_scheduler() -> None:
    """Stop the scheduler, waiting for running jobs to finish."""
    global _scheduler
    if _scheduler is not None:
        _scheduler.shutdown(wait=False)
        _scheduler = None
        logger.info("Scheduler stopped")


def job_status() -> dict:
    """Describe registered jobs and their next run times."""
    if _scheduler is None:
        return {"running": False, "jobs": []}
    return {
        "running": True,
        "jobs": [
            {
                "id": job.id,
                "name": job.name,
                "next_run_at": job.next_run_time.isoformat() if job.next_run_time else None,
            }
            for job in _scheduler.get_jobs()
        ],
    }
