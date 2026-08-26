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
import re
from datetime import datetime, timedelta, timezone

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models import Stock
from app.services.feeds import fetch_feed, strip_html
from app.services.matching import CompanyIndex, build_index, match_tickers
from app.services.ingest import IngestReport, RawArticle, store_articles

logger = logging.getLogger(__name__)

FEED_URL = "https://feeds.finance.yahoo.com/rss/2.0/headline"
SOURCE = "yahoo_news"
REQUEST_DELAY_SECONDS = 0.4


async def fetch_ticker_news(
    client: httpx.AsyncClient,
    ticker: str,
    cutoff: datetime,
    index: CompanyIndex | None = None,
) -> list[RawArticle]:
    """Headlines for one symbol, newer than the cutoff and actually about it.

    Requesting the feed per symbol looks like it makes attribution exact. It
    does not. Yahoo answers ``?s=AMZN`` with the market commentary it thinks an
    Amazon holder might read, so the symbol's feed carried "Polestar Announces
    Change to Board of Directors", "Billionaire David Tepper Sold Every Single
    Share of UnitedHealth" and "Bull of the Day: Carter's (CRI)" — the last of
    which scored +1.00 and was stored as positive news for Amazon.

    That is worse than noise in the feed. Sentiment is a pillar of the ranked
    score, so another company's good news lifted this one's ranking, and the
    same article would put it on the shortlist as a symbol with a catalyst.

    So each entry has to mention the company it was fetched for. The matcher
    already indexes every listing by name and symbol for the newswire sources,
    which is the same problem — a shared feed naming many companies — arrived
    at from the other direction.
    """
    entries = await fetch_feed(
        client,
        FEED_URL,
        params={"s": ticker, "region": "US", "lang": "en-US"},
    )

    articles: list[RawArticle] = []
    for entry in entries:
        if entry.published_at < cutoff:
            continue
        body = strip_html(entry.summary)
        if index is not None and not _is_about(ticker, entry.title, body, index):
            logger.debug("Yahoo %s feed carried an unrelated story: %s", ticker, entry.title)
            continue
        articles.append(
            RawArticle(
                ticker=ticker,
                headline=entry.title,
                body=body,
                url=entry.link,
                source=SOURCE,
                published_at=entry.published_at,
            )
        )
    return articles


def _is_about(ticker: str, headline: str, body: str | None, index: CompanyIndex) -> bool:
    """Does this story actually name the company whose feed it came from?

    Deliberately generous: the headline *or* the body counts, and matching is
    on the company index rather than a substring, so "Takeda Pharmaceutical
    Co." is found from a headline that only says "Takeda". The cost of being
    strict is a real story dropped; the cost of being loose is another
    company's news scored against this symbol, and only one of those two
    quietly changes a ranking.
    """
    symbol = ticker.upper()
    text = f"{headline} {body or ''}"
    if symbol in match_tickers(text, index, limit=10):
        return True
    # A headline can quote the bare symbol without the suffix the feed uses:
    # "AZN posts trial win" for AZN.L. The root is enough on its own only when
    # it is not a short, common word.
    root = symbol.partition(".")[0]
    if len(root) >= 3 and re.search(rf"\b{re.escape(root)}\b", text, re.IGNORECASE):
        return True
    return False


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

    # Built once for the whole run: it is a scan of the universe, and doing it
    # per symbol would cost one query per request.
    company_index = await build_index(db)

    collected: list[RawArticle] = []
    async with httpx.AsyncClient() as client:
        for position, symbol in enumerate(symbols):
            collected.extend(
                await fetch_ticker_news(client, symbol, cutoff, company_index)
            )
            if position < len(symbols) - 1:
                await asyncio.sleep(REQUEST_DELAY_SECONDS)

    if not collected:
        return IngestReport()

    report = await store_articles(db, collected)
    logger.info("Yahoo news: %s symbols, %s", len(symbols), report.as_dict())
    return report
