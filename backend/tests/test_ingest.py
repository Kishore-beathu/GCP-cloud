"""Ingestion: deduplication, scoring, and alert firing."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import func, select

from app.models import AlertHistory, AlertType, NewsArticle, SentimentScore, UserAlert
from app.services.ingest import RawArticle, store_articles

pytestmark = pytest.mark.asyncio


def _article(ticker: str = "MRNA", url: str = "https://example.com/a1", **overrides):
    defaults = dict(
        ticker=ticker,
        headline="FDA approves Moderna vaccine after priority review",
        url=url,
        source="test_feed",
        published_at=datetime.now(timezone.utc),
        body=None,
    )
    defaults.update(overrides)
    return RawArticle(**defaults)


async def test_stores_article_with_sentiment(db, seeded_stocks):
    report = await store_articles(db, [_article()])

    assert report.added == 1
    score = (await db.execute(select(SentimentScore))).scalar_one()
    assert score.sentiment == "positive"
    assert score.event_type == "fda_approval"


async def test_duplicate_url_from_same_source_is_skipped(db, seeded_stocks):
    await store_articles(db, [_article()])
    report = await store_articles(db, [_article()])

    assert report.added == 0
    assert report.skipped_duplicate == 1
    assert (await db.execute(select(func.count(NewsArticle.id)))).scalar_one() == 1


async def test_duplicates_within_one_batch_are_collapsed(db, seeded_stocks):
    report = await store_articles(db, [_article(), _article()])

    assert report.added == 1
    assert report.skipped_duplicate == 1


async def test_second_wire_carrying_the_same_story_is_merged(db, seeded_stocks):
    """Two wires, one release. Previously two rows; now one story with a copy.

    The copy is kept rather than dropped — that several sources carried it is
    corroboration — but it does not stand alone in the feed, is not scored, and
    is not counted twice by the backtester.
    """
    await store_articles(db, [_article(source="feed_a")])
    report = await store_articles(db, [_article(source="feed_b")])

    assert report.added == 0
    assert report.merged_duplicate == 1

    rows = (await db.execute(select(NewsArticle).order_by(NewsArticle.id))).scalars().all()
    assert len(rows) == 2
    assert rows[0].duplicate_of_id is None
    assert rows[1].duplicate_of_id == rows[0].id

    # Only the primary carries a score, so sentiment is counted once.
    assert (await db.execute(select(func.count(SentimentScore.id)))).scalar_one() == 1


async def test_a_different_story_for_the_same_ticker_is_not_merged(db, seeded_stocks):
    """The dangerous failure is a wrong merge, which hides a real event."""
    await store_articles(db, [_article(source="feed_a")])
    report = await store_articles(
        db,
        [
            _article(
                source="feed_b",
                url="https://example.com/a2",
                headline="Moderna prices $1 billion convertible note offering",
            )
        ],
    )

    assert report.added == 1
    assert report.merged_duplicate == 0


async def test_the_same_story_outside_the_window_is_not_merged(db, seeded_stocks):
    """A re-run of the same news a week later is a new event, not a copy."""
    old = datetime.now(timezone.utc) - timedelta(days=7)
    await store_articles(db, [_article(source="feed_a", published_at=old)])
    report = await store_articles(db, [_article(source="feed_b", url="https://example.com/a2")])

    assert report.added == 1
    assert report.merged_duplicate == 0


async def test_merged_copies_are_hidden_from_the_feed_but_counted(client, db, seeded_stocks):
    await store_articles(db, [_article(source="feed_a")])
    await store_articles(db, [_article(source="feed_b", url="https://example.com/a2")])

    rows = (await client.get("/news")).json()
    assert len(rows) == 1
    assert rows[0]["corroborations"] == 1
    assert rows[0]["other_sources"] == ["feed_b"]

    assert len((await client.get("/news?include_duplicates=true")).json()) == 2


async def test_untracked_ticker_is_skipped(db, seeded_stocks):
    report = await store_articles(db, [_article(ticker="NOTREAL")])

    assert report.added == 0
    assert report.skipped_unknown_ticker == 1


async def test_empty_batch_is_a_noop(db, seeded_stocks):
    report = await store_articles(db, [])
    assert report.as_dict() == {
        "added": 0,
        "skipped_duplicate": 0,
        "skipped_unknown_ticker": 0,
        "alerts_triggered": 0,
        "merged_duplicate": 0,
    }


async def test_positive_news_alert_fires(db, seeded_stocks):
    mrna = seeded_stocks[0]
    db.add(
        UserAlert(
            user_id="local",
            ticker_id=mrna.id,
            alert_type=AlertType.POSITIVE_NEWS.value,
            condition={"min_score": 0.5},
            channels=["in_app"],
        )
    )
    await db.commit()

    report = await store_articles(db, [_article()])

    assert report.alerts_triggered == 1
    history = (await db.execute(select(AlertHistory))).scalars().all()
    assert len(history) == 1
    assert history[0].payload["sentiment"] == "positive"


async def test_alert_on_other_ticker_does_not_fire(db, seeded_stocks):
    pfe = seeded_stocks[1]
    db.add(
        UserAlert(
            user_id="local",
            ticker_id=pfe.id,
            alert_type=AlertType.POSITIVE_NEWS.value,
            condition={},
            channels=["in_app"],
        )
    )
    await db.commit()

    report = await store_articles(db, [_article(ticker="MRNA")])
    assert report.alerts_triggered == 0


async def test_inactive_alert_does_not_fire(db, seeded_stocks):
    mrna = seeded_stocks[0]
    db.add(
        UserAlert(
            user_id="local",
            ticker_id=mrna.id,
            alert_type=AlertType.POSITIVE_NEWS.value,
            condition={},
            channels=["in_app"],
            is_active=False,
        )
    )
    await db.commit()

    report = await store_articles(db, [_article()])
    assert report.alerts_triggered == 0


async def test_event_type_alert_matches_only_its_event(db, seeded_stocks):
    mrna = seeded_stocks[0]
    db.add(
        UserAlert(
            user_id="local",
            ticker_id=mrna.id,
            alert_type=AlertType.EVENT_TYPE.value,
            condition={"event_type": "merger_acquisition"},
            channels=["in_app"],
        )
    )
    await db.commit()

    # An FDA approval must not satisfy an M&A alert.
    report = await store_articles(db, [_article()])
    assert report.alerts_triggered == 0

    report = await store_articles(
        db,
        [
            _article(
                url="https://example.com/a2",
                headline="Moderna to acquire biotech in all-cash merger",
                published_at=datetime.now(timezone.utc) - timedelta(hours=1),
            )
        ],
    )
    assert report.alerts_triggered == 1


async def test_confidence_threshold_blocks_weak_signals(db, seeded_stocks):
    mrna = seeded_stocks[0]
    db.add(
        UserAlert(
            user_id="local",
            ticker_id=mrna.id,
            alert_type=AlertType.POSITIVE_NEWS.value,
            condition={"min_confidence": 0.99},
            channels=["in_app"],
        )
    )
    await db.commit()

    report = await store_articles(db, [_article()])
    assert report.alerts_triggered == 0
