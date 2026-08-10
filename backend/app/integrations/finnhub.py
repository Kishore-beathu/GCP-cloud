"""Finnhub company-news ingestion.

One ``/company-news`` call covers one ticker over a date range, so the
scheduler rotates through the universe in batches sized to the account's
rate limit (free tier: ~60 calls/min).

Requires ``FINNHUB_API_KEY``; without it every entry point logs once and
returns an empty report so the rest of the platform keeps running.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import date, datetime, timedelta, timezone

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models import Stock
from app.services.ingest import IngestReport, RawArticle, store_articles

logger = logging.getLogger(__name__)

BASE_URL = "https://finnhub.io/api/v1"
SOURCE = "finnhub"
REQUEST_DELAY_SECONDS = 1.1  # ~55 calls/min, inside the free tier's 60


class FinnhubRateLimited(Exception):
    """Raised when Finnhub answers 429; the current batch should stop."""


class FinnhubRejected(Exception):
    """Raised on 401/403: a bad key, or an endpoint the plan does not include.

    Distinct from rate limiting because the remedy is different and the batch
    must not continue: repeating a rejected call across 87 symbols produces 87
    identical warnings and an empty result that looks like "no news".
    """


def _parse_news_item(ticker: str, item: dict) -> RawArticle | None:
    """Convert one Finnhub news payload entry, or None if it is unusable."""
    headline = str(item.get("headline") or "").strip()
    url = str(item.get("url") or "").strip()
    published_unix = item.get("datetime")
    if not headline or not url or not published_unix:
        return None

    try:
        published_at = datetime.fromtimestamp(int(published_unix), tz=timezone.utc)
    except (ValueError, OSError, OverflowError):
        logger.debug("Finnhub item for %s has invalid timestamp %r", ticker, published_unix)
        return None

    summary = str(item.get("summary") or "").strip() or None
    return RawArticle(
        ticker=ticker,
        headline=headline,
        body=summary,
        url=url,
        source=SOURCE,
        published_at=published_at,
    )


async def fetch_company_news(
    client: httpx.AsyncClient,
    ticker: str,
    api_key: str,
    from_date: date,
    to_date: date,
) -> list[RawArticle]:
    """Fetch one ticker's news window. Raises FinnhubRateLimited on 429."""
    try:
        response = await client.get(
            f"{BASE_URL}/company-news",
            params={
                "symbol": ticker,
                "from": from_date.isoformat(),
                "to": to_date.isoformat(),
                "token": api_key,
            },
            timeout=30.0,
        )
        if response.status_code == 429:
            raise FinnhubRateLimited(ticker)
        if response.status_code in (401, 403):
            raise FinnhubRejected(
                f"HTTP {response.status_code} for {ticker}: {response.text[:200]}"
            )
        response.raise_for_status()
        payload = response.json()
    except (FinnhubRateLimited, FinnhubRejected):
        raise
    except (httpx.HTTPError, ValueError) as exc:
        logger.warning("Finnhub request failed for %s: %s", ticker, exc)
        return []

    if not isinstance(payload, list):
        logger.warning("Finnhub returned unexpected payload for %s: %r", ticker, type(payload))
        return []

    articles = []
    for item in payload:
        article = _parse_news_item(ticker, item)
        if article is not None:
            articles.append(article)
    return articles


async def ingest_finnhub_news(
    db: AsyncSession,
    tickers: list[str] | None = None,
    lookback_days: int | None = None,
) -> IngestReport:
    """Fetch and store news for the given tickers (default: all active)."""
    settings = get_settings()
    if not settings.finnhub_api_key:
        logger.info("Finnhub ingest skipped: FINNHUB_API_KEY is not set")
        return IngestReport()

    query = select(Stock.ticker).where(Stock.is_active.is_(True)).order_by(Stock.ticker)
    if tickers:
        query = query.where(Stock.ticker.in_([t.upper() for t in tickers]))
    symbols = list((await db.execute(query)).scalars())
    if not symbols:
        logger.info("Finnhub ingest: no matching stocks")
        return IngestReport()

    to_date = datetime.now(timezone.utc).date()
    from_date = to_date - timedelta(days=lookback_days or settings.finnhub_lookback_days)

    collected: list[RawArticle] = []
    async with httpx.AsyncClient() as client:
        for index, symbol in enumerate(symbols):
            try:
                collected.extend(
                    await fetch_company_news(
                        client, symbol, settings.finnhub_api_key, from_date, to_date
                    )
                )
            except FinnhubRateLimited:
                logger.warning(
                    "Finnhub rate limit hit at %s (%d/%d); storing what we have",
                    symbol,
                    index,
                    len(symbols),
                )
                break
            except FinnhubRejected as exc:
                logger.error(
                    "Finnhub rejected the request, stopping the batch: %s. "
                    "Check FINNHUB_API_KEY and what your plan covers.",
                    exc,
                )
                break
            if index < len(symbols) - 1:
                await asyncio.sleep(REQUEST_DELAY_SECONDS)

    return await store_articles(db, collected)
