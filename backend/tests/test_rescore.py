"""Re-scoring stored articles after a lexicon change."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from sqlalchemy import func, select

from app.models import NewsArticle, SentimentScore
from app.services.rescore import repair_article_links, rescore_articles, stale_count
from app.services.sentiment import get_analyzer

pytestmark = pytest.mark.asyncio


async def _stale_article(db, stock, headline: str) -> SentimentScore:
    """Store an article carrying a deliberately wrong, older-version score."""
    article = NewsArticle(
        ticker_id=stock.id,
        headline=headline,
        source="test_feed",
        url=f"https://example.com/{abs(hash(headline))}",
        published_at=datetime.now(timezone.utc),
    )
    db.add(article)
    await db.flush()

    score = SentimentScore(
        article_id=article.id,
        sentiment="negative",          # what the old buggy lexicon produced
        score=-1.0,
        confidence=0.7,
        event_type="other",
        event_confidence=0.0,
        model_version="lexicon-v1",
    )
    db.add(score)
    await db.commit()
    return score


async def test_status_reports_stale_rows(db, seeded_stocks):
    await _stale_article(db, seeded_stocks[0], "Regulatory submission accepted for review")

    status = await stale_count(db)
    assert status["current_model"] == get_analyzer().model_version
    assert status["by_model_version"]["lexicon-v1"] == 1
    assert status["stale"] == 1


async def test_rescore_corrects_a_wrongly_negative_article(db, seeded_stocks):
    """The exact failure the old lexicon had: 'submission' matched 'miss'."""
    score = await _stale_article(
        db, seeded_stocks[0], "Regulatory submission accepted for review"
    )
    assert score.sentiment == "negative"

    report = await rescore_articles(db)
    assert report.examined == 1
    assert report.updated == 1
    assert report.sentiment_flipped == 1

    refreshed = (await db.execute(select(SentimentScore))).scalar_one()
    assert refreshed.sentiment == "positive"
    assert refreshed.model_version == get_analyzer().model_version


async def test_rescore_leaves_current_rows_alone(db, seeded_stocks):
    await _stale_article(db, seeded_stocks[0], "Regulatory submission accepted for review")
    await rescore_articles(db)

    # Second pass finds nothing stale left.
    again = await rescore_articles(db)
    assert again.examined == 0
    assert again.updated == 0
    assert (await stale_count(db))["stale"] == 0


async def test_rescore_all_can_revisit_current_rows(db, seeded_stocks):
    await _stale_article(db, seeded_stocks[0], "Regulatory submission accepted for review")
    await rescore_articles(db)

    report = await rescore_articles(db, only_stale=False)
    assert report.examined == 1
    assert report.unchanged == 1
    assert report.updated == 0


async def test_rescore_respects_the_limit(db, seeded_stocks):
    for index in range(3):
        await _stale_article(db, seeded_stocks[0], f"FDA approves therapy number {index}")

    report = await rescore_articles(db, limit=2)
    assert report.examined == 2
    assert (await stale_count(db))["stale"] == 1


async def test_rescore_does_not_fire_alerts(db, seeded_stocks, client):
    """Replaying months of history into Slack would be worse than useless."""
    created = await client.post(
        "/alerts", json={"ticker": "MRNA", "alert_type": "positive_news"}
    )
    assert created.status_code == 201

    await _stale_article(db, seeded_stocks[0], "Regulatory submission accepted for review")
    await rescore_articles(db)

    history = await client.get("/alerts/history")
    assert history.json() == []


# --- Link repair --------------------------------------------------------------


async def _article(db, stock, url: str, index: int = 0):
    from datetime import datetime, timezone

    article = NewsArticle(
        ticker_id=stock.id,
        headline=f"Story {index}",
        source="yahoo_news",
        url=url,
        published_at=datetime.now(timezone.utc),
    )
    db.add(article)
    await db.commit()
    await db.refresh(article)
    return article


@pytest.mark.asyncio
async def test_repair_reports_unusable_links_without_deleting_by_default(db, seeded_stocks):
    """"Delete some of the news" must be a decision, not a side effect."""
    stock = seeded_stocks[0]
    await _article(db, stock, "yahoo-a1b2c3", 0)
    await _article(db, stock, "https://finance.yahoo.com/news/real.html", 1)

    report = await repair_article_links(db)

    assert report.examined == 2
    assert report.unusable == 1
    assert report.deleted == 0
    assert report.samples == ["yahoo-a1b2c3"]
    assert (await db.execute(select(func.count(NewsArticle.id)))).scalar_one() == 2


@pytest.mark.asyncio
async def test_repair_deletes_only_the_unusable_rows_when_applied(db, seeded_stocks):
    stock = seeded_stocks[0]
    await _article(db, stock, "yahoo-a1b2c3", 0)
    await _article(db, stock, "/news/relative", 1)
    kept = await _article(db, stock, "https://finance.yahoo.com/news/real.html", 2)

    report = await repair_article_links(db, apply=True)

    assert report.deleted == 2
    remaining = (await db.execute(select(NewsArticle.id))).scalars().all()
    assert remaining == [kept.id]


@pytest.mark.asyncio
async def test_repair_takes_the_sentiment_score_with_it(db, seeded_stocks):
    """An orphaned score would keep counting toward the sentiment pillar."""
    stock = seeded_stocks[0]
    article = await _article(db, stock, "yahoo-a1b2c3", 0)
    db.add(
        SentimentScore(
            article_id=article.id,
            sentiment="positive",
            score=0.9,
            confidence=0.8,
            event_type="other",
            event_confidence=0.5,
            model_version="test",
        )
    )
    await db.commit()

    await repair_article_links(db, apply=True)

    assert (await db.execute(select(func.count(SentimentScore.id)))).scalar_one() == 0


@pytest.mark.asyncio
async def test_a_syndicated_copy_is_released_when_its_primary_is_deleted(db, seeded_stocks):
    """Otherwise the copy points at a row that no longer exists.

    A duplicate is excluded from scoring by having a non-NULL duplicate_of_id.
    Left dangling it would stay excluded forever — a duplicate of nothing,
    invisible to the sentiment pillar with no way to notice.
    """
    stock = seeded_stocks[0]
    primary = await _article(db, stock, "yahoo-a1b2c3", 0)
    copy = await _article(db, stock, "https://wire.example.com/copy", 1)
    copy.duplicate_of_id = primary.id
    await db.commit()

    await repair_article_links(db, apply=True)
    await db.refresh(copy)

    assert copy.duplicate_of_id is None


# --- Attribution audit -------------------------------------------------------


@pytest.mark.asyncio
async def test_the_audit_finds_news_filed_under_the_wrong_company(db, seeded_stocks):
    """Rows written before the per-symbol feed was filtered are still scored."""
    from app.services.rescore import audit_attribution

    stock = seeded_stocks[0]  # MRNA / Moderna
    db.add_all(
        [
            NewsArticle(
                ticker_id=stock.id,
                headline="Bull of the Day: Carter's (CRI)",
                url="https://example.com/wrong",
                source="yahoo_news",
                published_at=datetime.now(timezone.utc),
            ),
            NewsArticle(
                ticker_id=stock.id,
                headline="Moderna reports positive Phase 3 data",
                url="https://example.com/right",
                source="yahoo_news",
                published_at=datetime.now(timezone.utc),
            ),
        ]
    )
    await db.commit()

    report = await audit_attribution(db)

    assert report.examined == 2
    assert report.misattributed == 1
    assert report.deleted == 0  # dry run by default
    assert report.by_ticker == {"MRNA": 1}
    assert report.samples[0]["headline"].startswith("Bull of the Day")


@pytest.mark.asyncio
async def test_applying_the_audit_removes_only_the_wrong_rows(db, seeded_stocks):
    from app.services.rescore import audit_attribution

    stock = seeded_stocks[0]
    db.add_all(
        [
            NewsArticle(
                ticker_id=stock.id,
                headline="An unrelated market roundup",
                url="https://example.com/wrong",
                source="yahoo_news",
                published_at=datetime.now(timezone.utc),
            ),
            NewsArticle(
                ticker_id=stock.id,
                headline="Moderna wins approval",
                url="https://example.com/right",
                source="yahoo_news",
                published_at=datetime.now(timezone.utc),
            ),
        ]
    )
    await db.commit()

    report = await audit_attribution(db, apply=True)

    assert report.deleted == 1
    remaining = (await db.execute(select(NewsArticle.headline))).scalars().all()
    assert remaining == ["Moderna wins approval"]


@pytest.mark.asyncio
async def test_the_audit_leaves_other_sources_alone(db, seeded_stocks):
    """SEC filings and newswire rows are matched at ingest by a different path."""
    from app.services.rescore import audit_attribution

    stock = seeded_stocks[0]
    db.add(
        NewsArticle(
            ticker_id=stock.id,
            headline="8-K filed",
            url="https://sec.example.com/1",
            source="sec_edgar",
            published_at=datetime.now(timezone.utc),
        )
    )
    await db.commit()

    report = await audit_attribution(db)

    assert report.examined == 0
    assert report.misattributed == 0


@pytest.mark.asyncio
async def test_finnhub_can_be_audited_without_being_pruned_by_default(db, seeded_stocks):
    """Measure before deleting, on the feed that is mostly right.

    Finnhub's company news carries the same filler shape — "Stay informed with
    the top movers within the S&P500 index on Monday" arrived filed under CIEN
    — but it is the better-attributed of the two sources, and a headline there
    can legitimately omit the company name. So it is measurable on request and
    excluded from the default sweep.
    """
    from app.services.rescore import audit_attribution

    stock = seeded_stocks[0]
    db.add(
        NewsArticle(
            ticker_id=stock.id,
            headline="Stay informed with the top movers within the S&P500 index",
            url="https://example.com/filler",
            source="finnhub",
            published_at=datetime.now(timezone.utc),
        )
    )
    await db.commit()

    default = await audit_attribution(db)
    assert default.examined == 0

    asked = await audit_attribution(db, sources=("finnhub",))
    assert asked.misattributed == 1
    assert asked.by_source == {"finnhub": 1}
    assert asked.samples[0]["source"] == "finnhub"
