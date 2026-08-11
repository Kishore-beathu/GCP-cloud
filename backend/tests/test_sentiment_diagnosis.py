"""The sentiment pillar can fail validation two ways; these tell them apart.

A pillar that separates nothing might be measuring real tone that happens not
to predict returns, or it might be barely varying across the universe — in
which case its percentiles are ties and sort order. Only the first is a reason
to argue about weights, so the difference has to be visible.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.models import NewsArticle, SentimentScore, Stock
from app.services.diagnostics import probe_sentiment_distribution


async def _stock(db, ticker: str) -> Stock:
    stock = Stock(ticker=ticker, company_name=f"{ticker} Inc", sector="pharma")
    db.add(stock)
    await db.commit()
    await db.refresh(stock)
    return stock


async def _article(db, stock: Stock, index: int, score: float | None) -> None:
    article = NewsArticle(
        ticker_id=stock.id,
        headline=f"Story {index} about {stock.ticker}",
        source="test",
        url=f"https://example.com/{stock.ticker}/{index}",
        published_at=datetime.now(timezone.utc) - timedelta(days=1),
    )
    db.add(article)
    if score is not None:
        db.add(
            SentimentScore(
                article=article,
                sentiment="neutral" if score == 0 else ("positive" if score > 0 else "negative"),
                score=score,
                confidence=0.8,
                event_type="other",
                event_confidence=0.5,
                model_version="test",
            )
        )
    await db.commit()


@pytest.mark.asyncio
async def test_an_empty_window_says_so_rather_than_dividing_by_zero(db):
    result = await probe_sentiment_distribution(db)

    assert result["articles"] == 0
    assert "detail" in result


@pytest.mark.asyncio
async def test_all_neutral_news_is_reported_as_a_flat_distribution(db):
    """The case the dashboard suggested: every headline scored exactly 0.00."""
    for index in range(6):
        stock = await _stock(db, f"F{index:02d}")
        await _article(db, stock, index, 0.0)

    result = await probe_sentiment_distribution(db)

    assert result["scored"] == 6
    assert result["scores"]["share_exactly_zero"] == 1.0
    assert result["scores"]["distinct_values"] == 1
    # The decisive number: no separation between symbols at all, so no
    # weighting of this pillar could rank anything.
    assert result["per_symbol_mean"]["spread"] == 0.0
    assert result["per_symbol_mean"]["stdev"] == 0.0


@pytest.mark.asyncio
async def test_varied_news_reports_real_separation(db):
    """The contrasting case, so a flat reading cannot be the only outcome."""
    for index in range(6):
        stock = await _stock(db, f"V{index:02d}")
        await _article(db, stock, index, (index - 3) * 0.25)

    result = await probe_sentiment_distribution(db)

    assert result["scores"]["distinct_values"] == 6
    assert result["per_symbol_mean"]["spread"] > 1.0
    assert result["per_symbol_mean"]["stdev"] > 0


@pytest.mark.asyncio
async def test_an_unscored_article_is_not_counted_as_neutral(db):
    """A scoring backlog and a neutral verdict must not look identical."""
    stock = await _stock(db, "UNS")
    await _article(db, stock, 0, None)
    await _article(db, stock, 1, 0.5)

    result = await probe_sentiment_distribution(db)

    assert result["articles"] == 2
    assert result["unscored"] == 1
    assert result["scored"] == 1
    assert "neutral" not in result["by_label"]


@pytest.mark.asyncio
async def test_duplicates_do_not_inflate_the_distribution(db):
    """One release on four wires is one opinion, not four."""
    stock = await _stock(db, "DUP")
    await _article(db, stock, 0, 0.9)

    from sqlalchemy import select

    primary = (await db.execute(select(NewsArticle))).scalars().first()
    db.add(
        NewsArticle(
            ticker_id=stock.id,
            headline="Syndicated copy",
            source="other_wire",
            url="https://example.com/copy",
            published_at=datetime.now(timezone.utc),
            duplicate_of_id=primary.id,
        )
    )
    await db.commit()

    result = await probe_sentiment_distribution(db)

    assert result["articles"] == 1
    assert result["articles_including_duplicates"] == 2


@pytest.mark.asyncio
async def test_endpoint_is_reachable(client, db):
    stock = await _stock(db, "EPT")
    await _article(db, stock, 0, 0.0)

    response = await client.get("/admin/diagnose/sentiment?days=30")

    assert response.status_code == 200
    assert response.json()["scored"] == 1
