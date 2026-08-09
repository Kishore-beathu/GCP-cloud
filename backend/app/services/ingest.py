"""Shared persistence path for every news source.

Every integration converts its payload into ``RawArticle`` and calls
``store_articles``. Deduplication, sentiment scoring, and alert evaluation
therefore behave identically no matter where an article came from.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import NewsArticle, SentimentScore, Stock
from app.services.alerts import evaluate_alerts_for_article
from app.services.sentiment import SentimentAnalyzer, get_analyzer

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class RawArticle:
    """Source-agnostic news item handed to the persistence layer."""

    ticker: str
    headline: str
    url: str
    source: str
    published_at: datetime
    body: str | None = None


@dataclass
class IngestReport:
    """Outcome of one ``store_articles`` call."""

    added: int = 0
    skipped_duplicate: int = 0
    skipped_unknown_ticker: int = 0
    alerts_triggered: int = 0

    def as_dict(self) -> dict[str, int]:
        return {
            "added": self.added,
            "skipped_duplicate": self.skipped_duplicate,
            "skipped_unknown_ticker": self.skipped_unknown_ticker,
            "alerts_triggered": self.alerts_triggered,
        }


async def _load_stock_map(db: AsyncSession, tickers: set[str]) -> dict[str, Stock]:
    if not tickers:
        return {}
    result = await db.execute(select(Stock).where(Stock.ticker.in_(tickers)))
    return {stock.ticker: stock for stock in result.scalars()}


async def _existing_urls(db: AsyncSession, articles: list[RawArticle]) -> set[tuple[str, str]]:
    """Return the (url, source) pairs already stored, for bulk dedup."""
    if not articles:
        return set()
    urls = {article.url for article in articles}
    result = await db.execute(
        select(NewsArticle.url, NewsArticle.source).where(NewsArticle.url.in_(urls))
    )
    return {(url, source) for url, source in result.all()}


async def store_articles(
    db: AsyncSession,
    articles: list[RawArticle],
    analyzer: SentimentAnalyzer | None = None,
) -> IngestReport:
    """Persist new articles with sentiment, then fire any matching alerts.

    Articles whose ticker is not in the ``stocks`` table are skipped — the
    watchlist defines what the platform tracks, and silently creating stocks
    from a news feed would let a typo'd symbol into the universe.
    """
    report = IngestReport()
    if not articles:
        return report

    analyzer = analyzer or get_analyzer()
    stocks = await _load_stock_map(db, {a.ticker.upper() for a in articles})
    seen = await _existing_urls(db, articles)
    stored: list[tuple[NewsArticle, SentimentScore]] = []

    for raw in articles:
        ticker = raw.ticker.upper()
        stock = stocks.get(ticker)
        if stock is None:
            report.skipped_unknown_ticker += 1
            logger.debug("Skipping article for untracked ticker %s", ticker)
            continue

        key = (raw.url, raw.source)
        if key in seen:
            report.skipped_duplicate += 1
            continue
        seen.add(key)

        article = NewsArticle(
            ticker_id=stock.id,
            headline=raw.headline,
            body=raw.body,
            source=raw.source,
            url=raw.url,
            published_at=raw.published_at,
        )
        db.add(article)

        sentiment = analyzer.analyze_sentiment(raw.headline, raw.body)
        event = analyzer.classify_event_type(raw.headline, raw.body)
        score = SentimentScore(
            article=article,
            sentiment=sentiment.sentiment.value,
            score=sentiment.score,
            confidence=sentiment.confidence,
            event_type=event.primary_event.value,
            event_confidence=event.confidence,
            model_version=sentiment.model_version,
        )
        db.add(score)
        stored.append((article, score))
        report.added += 1

    if not stored:
        return report

    # Flush so every article has a primary key before alerts reference it.
    await db.flush()

    for article, score in stored:
        report.alerts_triggered += await evaluate_alerts_for_article(db, article, score)

    await db.commit()
    logger.info("Ingest complete: %s", report.as_dict())
    return report
