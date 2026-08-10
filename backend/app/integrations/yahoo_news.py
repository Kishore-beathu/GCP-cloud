"""Per-ticker headlines from Yahoo, including the listings nothing else covers.

Finnhub's free tier carries company news for US symbols. That leaves the
European and Asia-Pacific half of this universe with SEC EDGAR as its only
source — and SEC covers US registrants, so those names had no news at all.

Yahoo publishes a headline feed per symbol, keyed on the same suffixed
symbology this universe already uses (``AZN.L``, ``4502.T``, ``068270.KS``),
which makes it the one free source that closes that gap without a symbol
translation table.

Same caveats as the price integration: undocumented, unguaranteed, and every
failure is soft. Rows are tagged ``yahoo_news`` so they can be identified and
removed if a licensed feed replaces them.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models import Stock
from app.services.feeds import fetch_feed, strip_html
from app.services.ingest import IngestReport, RawArticle, store_articles

logger = logging.getLogger(__name__)

FEED_URL = "https://feeds.finance.yahoo.com/rss/2.0/headline"
SOURCE = "yahoo_news"
REQUEST_DELAY_SECONDS = 0.4


async def fetch_ticker_news(
    client: httpx.AsyncClient, ticker: str, cutoff: datetime
) -> list[RawArticle]:
    """Headlines for one symbol, newer than the cutoff."""
    entries = await fetch_feed(
        client,
        FEED_URL,
        params={"s": ticker, "region": "US", "lang": "en-US"},
    )

    articles: list[RawArticle] = []
    for entry in entries:
        if entry.published_at < cutoff:
            continue
        articles.append(
            RawArticle(
                # The feed was requested per symbol, so attribution is exact —
                # no company-name matching, and no false positives from it.
                ticker=ticker,
                headline=entry.title,
                body=strip_html(entry.summary),
                url=entry.link,
                source=SOURCE,
                published_at=entry.published_at,
            )
        )
    return articles


async def ingest_yahoo_news(
    db: AsyncSession,
    tickers: list[str] | None = None,
    lookback_days: int | None = None,
) -> IngestReport:
    """Fetch headlines for the given symbols (default: all active)."""
    settings = get_settings()
    if not settings.yahoo_news_enabled:
        logger.info("Yahoo news skipped: YAHOO_NEWS_ENABLED is false")
        return IngestReport()

    query = select(Stock.ticker).where(Stock.is_active.is_(True)).order_by(Stock.ticker)
    if tickers:
        query = query.where(Stock.ticker.in_([t.upper() for t in tickers]))
    symbols = list((await db.execute(query)).scalars())
    if not symbols:
        return IngestReport()

    cutoff = datetime.now(timezone.utc) - timedelta(
        days=lookback_days or settings.yahoo_news_lookback_days
    )

    collected: list[RawArticle] = []
    async with httpx.AsyncClient() as client:
        for index, symbol in enumerate(symbols):
            collected.extend(await fetch_ticker_news(client, symbol, cutoff))
            if index < len(symbols) - 1:
                await asyncio.sleep(REQUEST_DELAY_SECONDS)

    if not collected:
        return IngestReport()

    report = await store_articles(db, collected)
    logger.info("Yahoo news: %s symbols, %s", len(symbols), report.as_dict())
    return report
