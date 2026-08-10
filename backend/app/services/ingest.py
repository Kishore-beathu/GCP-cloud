"""Shared persistence path for every news source.

Every integration converts its payload into ``RawArticle`` and calls
``store_articles``. Deduplication, sentiment scoring, and alert evaluation
therefore behave identically no matter where an article came from.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import NewsArticle, SentimentScore, Stock
from app.services.alerts import PendingNotification, deliver_all, evaluate_alerts_for_article
from app.services.dedup import DEFAULT_WINDOW, is_duplicate
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
    # Stored, but linked to an earlier copy of the same story rather than
    # standing alone. Counted separately from skipped_duplicate, which is an
    # exact re-fetch of a URL already held.
    merged_duplicate: int = 0

    def as_dict(self) -> dict[str, int]:
        return {
            "added": self.added,
            "skipped_duplicate": self.skipped_duplicate,
            "skipped_unknown_ticker": self.skipped_unknown_ticker,
            "alerts_triggered": self.alerts_triggered,
            "merged_duplicate": self.merged_duplicate,
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


async def _recent_by_ticker(
    db: AsyncSession,
    articles: list[RawArticle],
    window: timedelta,
    ticker_ids: set[int],
) -> dict[int, list[NewsArticle]]:
    """Articles already stored for these tickers inside the merge window.

    Scoped to the tickers in this batch. Without that filter the query reads
    every article in the window across the whole universe on every ingest —
    invisible on a fresh database and steadily worse as one fills up, on a path
    that eight sources now run several times a minute.

    Only primaries are returned: a duplicate always points at the earliest
    copy, so chains never form and the corroboration count stays meaningful.
    """
    if not articles or not ticker_ids:
        return {}

    earliest = min(article.published_at for article in articles) - window
    if earliest.tzinfo is None:
        earliest = earliest.replace(tzinfo=timezone.utc)

    rows = (
        await db.execute(
            select(NewsArticle).where(
                NewsArticle.ticker_id.in_(ticker_ids),
                NewsArticle.published_at >= earliest,
                NewsArticle.duplicate_of_id.is_(None),
            )
        )
    ).scalars()

    by_ticker: dict[int, list[NewsArticle]] = {}
    for row in rows:
        by_ticker.setdefault(row.ticker_id, []).append(row)
    return by_ticker


def _find_primary(
    candidates: list[NewsArticle], raw: RawArticle, window: timedelta
) -> NewsArticle | None:
    """The earlier article this one is a retelling of, if any."""
    published = raw.published_at
    if published.tzinfo is None:
        published = published.replace(tzinfo=timezone.utc)

    for candidate in candidates:
        existing = candidate.published_at
        if existing.tzinfo is None:
            existing = existing.replace(tzinfo=timezone.utc)
        if abs(existing - published) > window:
            continue
        if is_duplicate(candidate.headline, raw.headline):
            return candidate
    return None


async def store_articles(
    db: AsyncSession,
    articles: list[RawArticle],
    analyzer: SentimentAnalyzer | None = None,
    merge_window: timedelta | None = None,
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
    window = merge_window if merge_window is not None else DEFAULT_WINDOW
    stocks = await _load_stock_map(db, {a.ticker.upper() for a in articles})
    seen = await _existing_urls(db, articles)
    recent = await _recent_by_ticker(
        db, articles, window, {stock.id for stock in stocks.values()}
    )
    stored: list[tuple[NewsArticle, SentimentScore, str]] = []
    # Resolved after the flush: a primary found earlier in this same batch has
    # no primary key yet, so the link cannot be set at construction time.
    duplicates: list[tuple[NewsArticle, NewsArticle]] = []

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

        # The same release from a second wire is stored and linked, not
        # dropped: corroboration is signal, and only the primary is scored,
        # alerted on and counted by the backtester.
        primary = _find_primary(recent.get(stock.id, []), raw, window)

        article = NewsArticle(
            ticker_id=stock.id,
            headline=raw.headline,
            body=raw.body,
            source=raw.source,
            url=raw.url,
            published_at=raw.published_at,
        )
        db.add(article)

        if primary is not None:
            duplicates.append((article, primary))
            report.merged_duplicate += 1
            continue

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
        stored.append((article, score, ticker))
        report.added += 1
        # Later items in this same batch can be duplicates of this one.
        recent.setdefault(stock.id, []).append(article)

    if not stored and not duplicates:
        return report

    # Flush so every article has a primary key before alerts reference it —
    # and before a duplicate can point at a primary created in this same batch.
    await db.flush()

    for duplicate, primary in duplicates:
        duplicate.duplicate_of_id = primary.id

    pending: list[PendingNotification] = []
    for article, score, ticker in stored:
        report.alerts_triggered += await evaluate_alerts_for_article(
            db, article, score, ticker, pending
        )

    await db.commit()
    # Slack/email go out only once the firing is durably recorded, and outside
    # the transaction so their latency cannot hold database locks.
    await deliver_all(pending)

    logger.info("Ingest complete: %s", report.as_dict())
    return report
