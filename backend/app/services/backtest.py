"""Historical impact analysis: did scored news actually move the price?

For each article the engine finds the last close at or before publication (the
baseline) and the first close at or after publication + N days, then reports the
percentage change grouped by event type and sentiment.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import EventType, NewsArticle, Sentiment, SentimentScore, Stock, StockPrice
from app.schemas import BacktestResponse, EventImpact

logger = logging.getLogger(__name__)

HORIZONS_DAYS = (1, 5, 30)
# The window that decides whether a signal was "right".
ACCURACY_HORIZON_DAYS = 5


def _mean(values: list[float]) -> float | None:
    return round(sum(values) / len(values), 4) if values else None


def _as_utc(value: datetime) -> datetime:
    """Normalise to an aware UTC datetime.

    PostgreSQL returns TIMESTAMPTZ as aware, SQLite hands back naive values for
    the same column. Everything stored is UTC, so naive means UTC here.
    """
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def _baseline_close(prices: list[tuple[datetime, float]], published_at: datetime) -> float | None:
    """Last close at or before publication."""
    candidates = [close for when, close in prices if when <= published_at]
    return candidates[-1] if candidates else None


def _future_close(
    prices: list[tuple[datetime, float]], published_at: datetime, days: int
) -> float | None:
    """First close at or after publication + ``days``."""
    target = published_at + timedelta(days=days)
    for when, close in prices:
        if when >= target:
            return close
    return None


async def run_backtest(db: AsyncSession, ticker: str, days: int) -> BacktestResponse:
    """Aggregate historical price impact for one ticker's scored news."""
    symbol = ticker.strip().upper()
    stock = (
        await db.execute(select(Stock).where(Stock.ticker == symbol))
    ).scalar_one_or_none()
    if stock is None:
        raise LookupError(f"Unknown ticker {symbol}")

    since = datetime.now(timezone.utc) - timedelta(days=days)

    rows = (
        await db.execute(
            select(NewsArticle, SentimentScore)
            .join(SentimentScore, SentimentScore.article_id == NewsArticle.id)
            .where(NewsArticle.ticker_id == stock.id, NewsArticle.published_at >= since)
            .order_by(NewsArticle.published_at)
        )
    ).all()

    prices = [
        (_as_utc(price.price_date), price.close)
        for price in (
            await db.execute(
                select(StockPrice)
                .where(StockPrice.ticker_id == stock.id, StockPrice.price_date >= since)
                .order_by(StockPrice.price_date)
            )
        ).scalars()
    ]

    # (event_type, sentiment) -> {horizon_days: [pct_change, ...]}
    buckets: dict[tuple[str, str], dict[int, list[float]]] = {}
    correct = 0
    directional = 0
    with_price_data = 0

    for article, score in rows:
        published_at = _as_utc(article.published_at)

        baseline = _baseline_close(prices, published_at)
        if baseline is None or baseline == 0:
            continue

        impacts: dict[int, float] = {}
        for horizon in HORIZONS_DAYS:
            close = _future_close(prices, published_at, horizon)
            if close is not None:
                impacts[horizon] = round((close - baseline) / baseline * 100, 4)

        if not impacts:
            continue
        with_price_data += 1

        bucket = buckets.setdefault((score.event_type, score.sentiment), {})
        for horizon, pct in impacts.items():
            bucket.setdefault(horizon, []).append(pct)

        # Accuracy only makes sense for directional calls, so neutral is excluded.
        accuracy_impact = impacts.get(ACCURACY_HORIZON_DAYS)
        if accuracy_impact is not None and score.sentiment != Sentiment.NEUTRAL.value:
            directional += 1
            expected_up = score.sentiment == Sentiment.POSITIVE.value
            if (accuracy_impact > 0) == expected_up:
                correct += 1

    analysis: list[EventImpact] = []
    for (event_type, sentiment), horizons in buckets.items():
        samples = horizons.get(ACCURACY_HORIZON_DAYS, [])
        bucket_accuracy = None
        if samples and sentiment != Sentiment.NEUTRAL.value:
            expected_up = sentiment == Sentiment.POSITIVE.value
            hits = sum(1 for pct in samples if (pct > 0) == expected_up)
            bucket_accuracy = round(hits / len(samples) * 100, 2)

        analysis.append(
            EventImpact(
                event_type=EventType(event_type),
                sentiment=Sentiment(sentiment),
                count=max(len(v) for v in horizons.values()),
                avg_impact_1d=_mean(horizons.get(1, [])),
                avg_impact_5d=_mean(horizons.get(5, [])),
                avg_impact_30d=_mean(horizons.get(30, [])),
                accuracy=bucket_accuracy,
            )
        )

    analysis.sort(key=lambda item: item.count, reverse=True)

    return BacktestResponse(
        ticker=symbol,
        period_days=days,
        articles_analysed=len(rows),
        articles_with_price_data=with_price_data,
        overall_sentiment_accuracy=(
            round(correct / directional * 100, 2) if directional else None
        ),
        analysis=analysis,
    )
