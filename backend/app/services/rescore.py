"""Re-score stored articles after a lexicon or model change.

Sentiment is stored, not recomputed on read, so improving the scorer does
nothing for the news already in the database. Every ``sentiment_scores`` row
records the ``model_version`` that produced it, which makes the stale rows
identifiable and this job possible.

Re-scoring deliberately does **not** re-fire alerts: those already fired (or
did not) at the time, and replaying months of history into someone's Slack
channel would be worse than useless.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import NewsArticle, SentimentScore, Stock
from app.services import sectors
from app.services.sentiment import SentimentAnalyzer, get_analyzer

logger = logging.getLogger(__name__)


@dataclass
class RescoreReport:
    """Outcome of one re-scoring pass."""

    examined: int = 0
    updated: int = 0
    unchanged: int = 0
    sentiment_flipped: int = 0

    def as_dict(self) -> dict[str, int]:
        return {
            "examined": self.examined,
            "updated": self.updated,
            "unchanged": self.unchanged,
            "sentiment_flipped": self.sentiment_flipped,
        }


async def stale_count(db: AsyncSession, analyzer: SentimentAnalyzer | None = None) -> dict:
    """How many stored scores came from an older model version."""
    analyzer = analyzer or get_analyzer()
    rows = (
        await db.execute(
            select(SentimentScore.model_version, func.count(SentimentScore.id))
            .group_by(SentimentScore.model_version)
        )
    ).all()
    by_version = {version: count for version, count in rows}
    current = analyzer.model_version
    return {
        "current_model": current,
        "by_model_version": by_version,
        "stale": sum(count for version, count in by_version.items() if version != current),
    }


async def rescore_articles(
    db: AsyncSession,
    limit: int = 1000,
    only_stale: bool = True,
    analyzer: SentimentAnalyzer | None = None,
) -> RescoreReport:
    """Re-run sentiment and event classification over stored articles.

    ``only_stale`` restricts the pass to rows scored by a different model
    version, which is the usual case after a lexicon change.
    """
    analyzer = analyzer or get_analyzer()
    report = RescoreReport()

    query = (
        # The symbol's sector comes along because the lexicon is sector-aware:
        # rescoring without it would quietly re-apply the pharma reading to
        # every storage and AI symbol and undo the overlay.
        select(NewsArticle, SentimentScore, Stock.sector)
        .join(SentimentScore, SentimentScore.article_id == NewsArticle.id)
        .join(Stock, Stock.id == NewsArticle.ticker_id)
        .order_by(NewsArticle.id)
        .limit(limit)
    )
    if only_stale:
        query = query.where(SentimentScore.model_version != analyzer.model_version)

    for article, score, sector in (await db.execute(query)).all():
        report.examined += 1

        group = sectors.group_for(sector)
        sentiment = analyzer.analyze_sentiment(article.headline, article.body, group)
        event = analyzer.classify_event_type(article.headline, article.body)

        changed = (
            score.sentiment != sentiment.sentiment.value
            or score.score != sentiment.score
            or score.event_type != event.primary_event.value
            or score.model_version != sentiment.model_version
        )
        if not changed:
            report.unchanged += 1
            continue

        if score.sentiment != sentiment.sentiment.value:
            report.sentiment_flipped += 1

        score.sentiment = sentiment.sentiment.value
        score.score = sentiment.score
        score.confidence = sentiment.confidence
        score.event_type = event.primary_event.value
        score.event_confidence = event.confidence
        score.model_version = sentiment.model_version
        report.updated += 1

    await db.commit()
    logger.info("Re-score complete: %s", report.as_dict())
    return report
