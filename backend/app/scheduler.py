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
from app.integrations.alpha_vantage import update_quotes
from app.integrations.finnhub import ingest_finnhub_news, update_finnhub_quotes
from app.integrations.clinical import ingest_clinical_and_regulatory
from app.integrations.edgar_firehose import ingest_recent_filings
from app.integrations.fda import ingest_fda
from app.integrations.halts import ingest_halts
from app.integrations.newswire import ingest_newswires
from app.integrations.yahoo import update_yahoo_prices
from app.integrations.yahoo_news import ingest_yahoo_news
from app.integrations.finnhub_stream import finnhub_stream
from app.integrations.sec import ingest_sec_filings
from app.models import NewsArticle, Stock, StockPrice
from app.services.streams import ticker_hub

logger = logging.getLogger(__name__)

_scheduler: AsyncIOScheduler | None = None

# Rotating cursors into the ticker universe: each run of a source picks up
# where its last run left off, so one pass never hits a whole 1000-symbol
# watchlist against a rate-limited API at once.
_sec_cursor = 0
_finnhub_cursor = 0
_quote_cursor = 0
_finnhub_quote_cursor = 0
_yahoo_cursor = 0
_yahoo_news_cursor = 0


async def _active_tickers(db) -> list[str]:
    return list(
        (
            await db.execute(
                select(Stock.ticker).where(Stock.is_active.is_(True)).order_by(Stock.ticker)
            )
        ).scalars()
    )


def _next_batch(tickers: list[str], cursor: int, size: int) -> tuple[list[str], int]:
    """Take ``size`` tickers starting at ``cursor``, wrapping around the list.

    Capped at one pass over the universe — repeating a ticker within a single
    batch would spend rate-limited API calls for nothing.
    """
    size = max(1, min(size, len(tickers)))
    start = cursor % len(tickers)
    batch = tickers[start : start + size]
    if len(batch) < size:
        batch += tickers[: size - len(batch)]
    return batch, (start + size) % len(tickers)


async def sec_ingest_job() -> None:
    """Ingest SEC filings for the next batch of tickers."""
    global _sec_cursor
    settings = get_settings()

    async with get_session_factory()() as db:
        tickers = await _active_tickers(db)
        if not tickers:
            logger.info("SEC job: no active tickers")
            return

        batch, _sec_cursor = _next_batch(tickers, _sec_cursor, settings.sec_ingest_batch_size)
        logger.info("SEC job: ingesting %d tickers starting at %s", len(batch), batch[0])
        report = await ingest_sec_filings(db, batch)
        logger.info("SEC job complete: %s", report.as_dict())


async def finnhub_news_job() -> None:
    """Ingest Finnhub company news for the next batch of tickers."""
    global _finnhub_cursor
    settings = get_settings()

    async with get_session_factory()() as db:
        tickers = await _active_tickers(db)
        if not tickers:
            return

        batch, _finnhub_cursor = _next_batch(tickers, _finnhub_cursor, settings.finnhub_batch_size)
        logger.info("Finnhub job: ingesting %d tickers starting at %s", len(batch), batch[0])
        report = await ingest_finnhub_news(db, batch)
        logger.info("Finnhub job complete: %s", report.as_dict())


async def quote_refresh_job() -> None:
    """Refresh Alpha Vantage quotes for the next batch of tickers.

    Subscribed WebSocket tickers jump the queue so live viewers always see the
    freshest data; the remaining slots rotate through the rest of the universe.
    """
    global _quote_cursor
    settings = get_settings()

    async with get_session_factory()() as db:
        tickers = await _active_tickers(db)
        if not tickers:
            return

        size = max(1, settings.alpha_vantage_batch_size)
        watched = sorted(ticker_hub.subscribed_tickers() & set(tickers))[:size]
        batch = list(watched)
        if len(batch) < size:
            rotation, _quote_cursor = _next_batch(tickers, _quote_cursor, size - len(batch))
            batch += [t for t in rotation if t not in batch]

        result = await update_quotes(db, batch)
        if result["inserted"] or result["updated"] or result["failed"]:
            logger.info("Quote job (%s): %s", ",".join(batch), result)


async def finnhub_quote_job() -> None:
    """Refresh prices from Finnhub for the next batch of tickers.

    This is the primary price source: ~60 calls/min covers the whole universe
    in a couple of minutes, where Alpha Vantage's free tier allows ~25 calls a
    *day* and cannot populate the watchlist even once.
    """
    global _finnhub_quote_cursor
    settings = get_settings()

    async with get_session_factory()() as db:
        tickers = await _active_tickers(db)
        if not tickers:
            return

        size = max(1, settings.finnhub_quote_batch_size)
        # Symbols someone is watching go first; the rest rotate.
        watched = sorted(ticker_hub.subscribed_tickers() & set(tickers))[:size]
        batch = list(watched)
        if len(batch) < size:
            rotation, _finnhub_quote_cursor = _next_batch(
                tickers, _finnhub_quote_cursor, size - len(batch)
            )
            batch += [t for t in rotation if t not in batch]

        result = await update_finnhub_quotes(db, batch)
        if result["inserted"] or result["updated"]:
            logger.info("Finnhub quote job: %s", result)


async def yahoo_price_job() -> None:
    """Load prices and history for the symbols no keyed vendor covers.

    Runs on a slow interval: one call carries a full history window, so there
    is nothing to gain from polling it like a quote feed, and it is a courtesy
    to an endpoint nobody is paying for.
    """
    global _yahoo_cursor
    settings = get_settings()

    async with get_session_factory()() as db:
        tickers = await _active_tickers(db)
        if not tickers:
            return

        size = max(1, settings.yahoo_price_batch_size)
        batch, _yahoo_cursor = _next_batch(tickers, _yahoo_cursor, size)
        result = await update_yahoo_prices(db, batch)
        if result["inserted"] or result["updated"]:
            logger.info("Yahoo price job: %s", result)


async def _run_and_log(name: str, coroutine_factory) -> None:
    """Run one source and log its report.

    Each source is wrapped so a failure in one cannot stop the others: these
    are six independent third parties, and any of them can be down.
    """
    async with get_session_factory()() as db:
        try:
            report = await coroutine_factory(db)
        except Exception:
            logger.exception("%s ingest failed", name)
            return
    if report.added or report.merged_duplicate:
        logger.info("%s: %s", name, report.as_dict())


async def edgar_firehose_job() -> None:
    """Catch new SEC filings within a minute or two of acceptance."""
    await _run_and_log("EDGAR firehose", ingest_recent_filings)


async def fda_job() -> None:
    await _run_and_log("FDA", ingest_fda)


async def newswire_job() -> None:
    await _run_and_log("Newswires", ingest_newswires)


async def halts_job() -> None:
    await _run_and_log("Trading halts", ingest_halts)


async def clinical_job() -> None:
    await _run_and_log("Clinical/regulatory", ingest_clinical_and_regulatory)


async def yahoo_news_job() -> None:
    """Rotate through the universe pulling per-symbol headlines."""
    global _yahoo_news_cursor
    settings = get_settings()

    async with get_session_factory()() as db:
        tickers = await _active_tickers(db)
        if not tickers:
            return
        size = max(1, settings.yahoo_news_batch_size)
        batch, _yahoo_news_cursor = _next_batch(tickers, _yahoo_news_cursor, size)
        try:
            report = await ingest_yahoo_news(db, batch)
        except Exception:
            logger.exception("Yahoo news ingest failed")
            return
    if report.added or report.merged_duplicate:
        logger.info("Yahoo news: %s", report.as_dict())


async def price_push_job() -> None:
    """Push the latest stored close to every subscribed WebSocket client.

    Symbols carried by the Finnhub trade stream are skipped: replaying a stored
    close over a live trade price would make the UI flicker between two
    different numbers. This job is the fallback for everything else — symbols
    outside the stream's cap, or whenever the stream is down.
    """
    tickers = ticker_hub.subscribed_tickers() - finnhub_stream.live_symbols()
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
    # API-keyed sources only register when their key is configured, so
    # /jobs/status reflects what is actually running.
    if settings.finnhub_api_key:
        scheduler.add_job(
            finnhub_news_job,
            IntervalTrigger(minutes=settings.finnhub_news_interval_minutes),
            id="finnhub_news",
            name="Finnhub news ingestion",
            max_instances=1,
            coalesce=True,
        )
    if settings.alpha_vantage_api_key:
        scheduler.add_job(
            quote_refresh_job,
            IntervalTrigger(seconds=settings.alpha_vantage_interval_seconds),
            id="quote_refresh",
            name="Alpha Vantage quote refresh",
            max_instances=1,
            coalesce=True,
        )
    if settings.finnhub_api_key and settings.finnhub_quote_enabled:
        scheduler.add_job(
            finnhub_quote_job,
            IntervalTrigger(seconds=settings.finnhub_quote_interval_seconds),
            id="finnhub_quotes",
            name="Finnhub quote refresh",
            max_instances=1,
            coalesce=True,
        )

    if settings.yahoo_prices_enabled:
        scheduler.add_job(
            yahoo_price_job,
            IntervalTrigger(minutes=settings.yahoo_price_interval_minutes),
            id="yahoo_prices",
            name="Yahoo price and history load",
            max_instances=1,
            coalesce=True,
        )

    for enabled, job, interval, job_id, name in (
        (
            settings.edgar_firehose_enabled,
            edgar_firehose_job,
            settings.edgar_firehose_interval_minutes,
            "edgar_firehose",
            "SEC EDGAR current filings",
        ),
        (settings.fda_enabled, fda_job, settings.fda_interval_minutes, "fda", "FDA"),
        (
            settings.yahoo_news_enabled,
            yahoo_news_job,
            settings.yahoo_news_interval_minutes,
            "yahoo_news",
            "Yahoo headlines",
        ),
        (
            settings.newswire_enabled,
            newswire_job,
            settings.newswire_interval_minutes,
            "newswires",
            "Newswire releases",
        ),
        (
            settings.halts_enabled,
            halts_job,
            settings.halts_interval_minutes,
            "halts",
            "Trading halts",
        ),
        (
            settings.clinical_enabled,
            clinical_job,
            settings.clinical_interval_minutes,
            "clinical",
            "Clinical and regulatory",
        ),
    ):
        if not enabled:
            continue
        scheduler.add_job(
            job,
            IntervalTrigger(minutes=interval),
            id=job_id,
            name=name,
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
