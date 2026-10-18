"""Backtest aggregation."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.models import StockPrice
from app.services.backtest import run_backtest
from app.services.ingest import RawArticle, store_articles

pytestmark = pytest.mark.asyncio


async def _add_price_series(db, ticker_id: int, start: datetime, closes: list[float]) -> None:
    """One daily close per element, starting at ``start``."""
    for offset, close in enumerate(closes):
        db.add(
            StockPrice(
                ticker_id=ticker_id,
                close=close,
                price_date=start + timedelta(days=offset),
                source="test",
            )
        )
    await db.commit()


async def test_positive_news_followed_by_gain_scores_full_accuracy(db, seeded_stocks):
    mrna = seeded_stocks[0]
    start = datetime.now(timezone.utc) - timedelta(days=40)

    # Flat at 100 until the news, then a steady climb to 110.
    await _add_price_series(db, mrna.id, start, [100.0] * 5 + [102.0, 104.0, 106.0, 108.0, 110.0] * 4)

    await store_articles(
        db,
        [
            RawArticle(
                ticker="MRNA",
                headline="FDA approves therapy after priority review",
                url="https://example.com/win",
                source="test_feed",
                published_at=start + timedelta(days=4),
            )
        ],
    )

    result = await run_backtest(db, "MRNA", days=90)

    assert result.ticker == "MRNA"
    assert result.articles_analysed == 1
    assert result.articles_with_price_data == 1
    assert result.overall_sentiment_accuracy == 100.0

    impact = result.analysis[0]
    assert impact.event_type.value == "fda_approval"
    assert impact.sentiment.value == "positive"
    assert impact.avg_impact_5d is not None and impact.avg_impact_5d > 0


async def test_positive_news_followed_by_drop_scores_zero_accuracy(db, seeded_stocks):
    mrna = seeded_stocks[0]
    start = datetime.now(timezone.utc) - timedelta(days=40)

    await _add_price_series(db, mrna.id, start, [100.0] * 5 + [95.0] * 20)

    await store_articles(
        db,
        [
            RawArticle(
                ticker="MRNA",
                headline="FDA approves therapy after priority review",
                url="https://example.com/lose",
                source="test_feed",
                published_at=start + timedelta(days=4),
            )
        ],
    )

    result = await run_backtest(db, "MRNA", days=90)
    assert result.overall_sentiment_accuracy == 0.0
    assert result.analysis[0].avg_impact_5d < 0


async def test_articles_without_price_history_are_excluded(db, seeded_stocks):
    await store_articles(
        db,
        [
            RawArticle(
                ticker="MRNA",
                headline="FDA approves therapy",
                url="https://example.com/no-prices",
                source="test_feed",
                published_at=datetime.now(timezone.utc) - timedelta(days=10),
            )
        ],
    )

    result = await run_backtest(db, "MRNA", days=90)
    assert result.articles_analysed == 1
    assert result.articles_with_price_data == 0
    assert result.analysis == []
    assert result.overall_sentiment_accuracy is None


async def test_unknown_ticker_raises_lookup_error(db, seeded_stocks):
    with pytest.raises(LookupError):
        await run_backtest(db, "NOSUCH", days=30)


async def test_articles_outside_window_are_ignored(db, seeded_stocks):
    await store_articles(
        db,
        [
            RawArticle(
                ticker="MRNA",
                headline="FDA approves therapy",
                url="https://example.com/old",
                source="test_feed",
                published_at=datetime.now(timezone.utc) - timedelta(days=400),
            )
        ],
    )

    result = await run_backtest(db, "MRNA", days=30)
    assert result.articles_analysed == 0
