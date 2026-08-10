"""Nasdaq trading halts: the earliest signal that something is coming.

A regulatory halt is published before the news it precedes. An LUDP halt is
already-visible volatility, but a **T1 — News Pending** halt means the exchange
has been told an announcement is imminent and has stopped trading so everyone
learns of it together. The release itself follows minutes later.

For a catalyst watchlist that ordering is the whole point: nothing else here
can tell you an announcement exists before it has been made.

The feed is keyed by symbol, so attribution is exact and no name matching is
involved — the cleanest signal-to-noise ratio of any source wired up here.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models import Stock
from app.services.feeds import fetch_feed
from app.services.ingest import IngestReport, RawArticle, store_articles

logger = logging.getLogger(__name__)

FEED_URL = "https://www.nasdaqtrader.com/rss.aspx?feed=tradehalts"
SOURCE = "halt"

# Reason codes, expanded because the lexicon scores words and "T1" is not one.
# The wording is chosen to carry the right sentiment: a news-pending halt is
# uncertainty, not bad news, and should not read as a collapse.
HALT_REASONS: dict[str, str] = {
    "T1": "news pending — an announcement is expected",
    "T2": "news released, trading resumption pending",
    "T5": "single-stock trading pause, price moved outside the volatility band",
    "T6": "extraordinary market activity, quotation under review",
    "T8": "exchange-traded product halted",
    "T12": "additional information requested by the exchange",
    "H4": "halted, non-compliance with exchange listing rules",
    "H9": "halted, filings not current",
    "H10": "halted by the SEC",
    "H11": "halted pending a regulatory filing",
    "D": "delisted",
    "LUDP": "volatility trading pause",
    "LUDS": "volatility trading pause, straddle condition",
    "MWC1": "market-wide circuit breaker, level 1",
    "MWC2": "market-wide circuit breaker, level 2",
    "MWC3": "market-wide circuit breaker, level 3",
    "IPO1": "IPO not yet trading",
    "M": "corporate action",
}

# Halts that say something about the company. Market-wide breakers and IPO
# codes fire for reasons unrelated to any one issuer, and attaching them to a
# ticker would put market noise into that company's news history.
COMPANY_SPECIFIC = frozenset(
    {"T1", "T2", "T6", "T12", "H4", "H9", "H10", "H11", "D", "M"}
)


def parse_halt_title(title: str) -> tuple[str | None, str | None]:
    """Read the symbol and reason code out of a halt entry.

    Titles are terse and inconsistently punctuated across the feed's history,
    so this reads tokens rather than trusting a fixed layout.
    """
    tokens = [token.strip(" -–—:;,|") for token in title.replace("|", " ").split()]
    tokens = [token for token in tokens if token]
    if not tokens:
        return None, None

    symbol = None
    reason = None
    for token in tokens:
        upper = token.upper()
        if reason is None and upper in HALT_REASONS:
            reason = upper
        elif symbol is None and upper.replace(".", "").isalpha() and 1 <= len(upper) <= 6:
            symbol = upper
    return symbol, reason


async def ingest_halts(db: AsyncSession, lookback_hours: int | None = None) -> IngestReport:
    """Store halts affecting tracked symbols."""
    settings = get_settings()
    if not settings.halts_enabled:
        logger.info("Halt ingest skipped: HALTS_ENABLED is false")
        return IngestReport()

    tracked = {
        ticker.upper()
        for ticker in (
            await db.execute(select(Stock.ticker).where(Stock.is_active.is_(True)))
        ).scalars()
    }
    if not tracked:
        return IngestReport()

    cutoff = datetime.now(timezone.utc) - timedelta(
        hours=lookback_hours or settings.halts_lookback_hours
    )

    collected: list[RawArticle] = []
    async with httpx.AsyncClient() as client:
        for entry in await fetch_feed(client, FEED_URL):
            if entry.published_at < cutoff:
                continue

            symbol, reason = parse_halt_title(entry.title)
            if not symbol or symbol not in tracked:
                continue
            if reason and reason not in COMPANY_SPECIFIC:
                continue

            explanation = HALT_REASONS.get(reason or "", "trading halted")
            collected.append(
                RawArticle(
                    ticker=symbol,
                    headline=f"{symbol} trading halted ({reason or 'unspecified'}): {explanation}",
                    body=entry.summary,
                    url=entry.link,
                    source=SOURCE,
                    published_at=entry.published_at,
                )
            )

    if not collected:
        return IngestReport()

    report = await store_articles(db, collected)
    logger.info("Halt ingest: %s", report.as_dict())
    return report
