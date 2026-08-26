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
from dataclasses import dataclass, field

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import NewsArticle, SentimentScore, Stock
from app.services.sentiment import SentimentAnalyzer, get_analyzer, overlay_key

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

        key = overlay_key(sector)
        sentiment = analyzer.analyze_sentiment(article.headline, article.body, key)
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


@dataclass
class LinkRepairReport:
    """What a link repair pass found, and what it did about it."""

    examined: int = 0
    unusable: int = 0
    deleted: int = 0
    samples: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "examined": self.examined,
            "unusable": self.unusable,
            "deleted": self.deleted,
            "samples": self.samples,
        }


async def repair_article_links(
    db: AsyncSession, apply: bool = False, limit: int = 20
) -> LinkRepairReport:
    """Find articles whose stored URL is not a URL, and optionally drop them.

    The feed parser used to accept a non-permalink ``<guid>`` as the article
    link, so rows exist carrying opaque vendor ids. Rendered as an ``href``
    those resolve against the dashboard's own origin, and clicking the
    headline goes nowhere. The parser no longer stores them; this clears the
    ones already written.

    Deleting is a last resort, not the recommended fix. The URL cannot be
    recovered from an opaque id, but the article's *sentiment* is unaffected
    and the scoring pillar reads it — and on a real database this was a fifth
    of the corpus. Yahoo's feed only serves recent items, so deleting the
    older ones loses history that no later ingest will bring back, thinning
    exactly the early validation periods that are already the sparsest. The
    dashboard renders an unusable link as plain text instead, which fixes the
    broken click without destroying the row behind it.

    What remains useful here is the count: it says how much of the stored news
    predates the parser fix. Apply the deletion only to clear a corpus small
    enough not to matter, or after re-ingesting.

    When it does delete, sentiment scores follow via ``ON DELETE CASCADE``, and
    other articles pointing at a deleted one as their duplicate primary have
    the pointer set to NULL rather than being orphaned.

    Defaults to a dry run, because "delete some of the news" should be a
    decision rather than a side effect of asking a question.
    """
    report = LinkRepairReport()

    rows = (await db.execute(select(NewsArticle.id, NewsArticle.url))).all()
    report.examined = len(rows)

    unusable = [
        (article_id, url)
        for article_id, url in rows
        if not (url or "").lower().startswith(("http://", "https://"))
    ]
    report.unusable = len(unusable)
    report.samples = [url for _, url in unusable[:limit]]

    if apply and unusable:
        await db.execute(
            delete(NewsArticle).where(NewsArticle.id.in_([i for i, _ in unusable]))
        )
        await db.commit()
        report.deleted = len(unusable)
        logger.info("Deleted %d articles with unusable links", report.deleted)

    return report


@dataclass
class AttributionReport:
    """What an attribution audit found in the stored Yahoo news."""

    examined: int = 0
    misattributed: int = 0
    deleted: int = 0
    sources: list[str] = field(default_factory=list)
    by_source: dict[str, int] = field(default_factory=dict)
    by_ticker: dict[str, int] = field(default_factory=dict)
    samples: list[dict] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "examined": self.examined,
            "misattributed": self.misattributed,
            "deleted": self.deleted,
            "sources": self.sources,
            "by_source": self.by_source,
            "by_ticker": self.by_ticker,
            "samples": self.samples,
        }


# Sources fetched one symbol at a time, where the request URL was taken as
# proof the story is about that symbol. Both vendors answer with a mix.
PER_SYMBOL_SOURCES = ("yahoo_news", "finnhub")


async def audit_attribution(
    db: AsyncSession,
    apply: bool = False,
    limit: int = 20,
    sources: tuple[str, ...] = ("yahoo_news",),
) -> AttributionReport:
    """Find stored Yahoo articles that never name the symbol they are filed under.

    Yahoo's per-symbol feed answers with what it thinks a holder of that symbol
    might want to read, not only with news about it, and the ingest trusted the
    request URL as attribution. So rows exist filed under one company and
    describing another — "Bull of the Day: Carter's (CRI)" scored +1.00 and
    stored against Amazon.

    Unlike the unusable-link repair, deleting here is the right call rather
    than a last resort. That one destroyed a usable sentiment score to fix a
    broken hyperlink; this one removes a score that is *wrong* — attributed to
    a company the article is not about, feeding a pillar of the ranked score
    and able to put a symbol on the shortlist on another company's good news.
    A missing article costs a little coverage. A misattributed one costs
    correctness, and does it invisibly.

    Still a dry run by default: it reports the scale and names examples
    first. That is also how a change to the matching rule gets measured — the
    audit calls the same matcher the ingest does, so tightening the rule and
    re-running the dry run says exactly how many stored rows the new rule
    would drop, and which, before anything is deleted.

    ``sources`` defaults to Yahoo alone, which is the one measured so far.
    Finnhub's company-news carries the same shape of filler — "Stay informed
    with the top movers within the S&P500 index on Monday" arrived under CIEN —
    but it is the better-attributed feed of the two, and a headline there can
    legitimately omit the company name ("Q2 earnings beat estimates"). So it is
    measurable on request and not swept up by default: measure before deleting.
    """
    from app.services.matching import build_index
    from app.integrations.yahoo_news import _is_about

    report = AttributionReport()
    index = await build_index(db)

    wanted = tuple(sources) or ("yahoo_news",)
    report.sources = list(wanted)
    rows = (
        await db.execute(
            select(
                NewsArticle.id,
                NewsArticle.headline,
                NewsArticle.body,
                NewsArticle.source,
                Stock.ticker,
            )
            .join(Stock, Stock.id == NewsArticle.ticker_id)
            .where(NewsArticle.source.in_(wanted))
        )
    ).all()
    report.examined = len(rows)

    wrong: list[int] = []
    for article_id, headline, body, source, ticker in rows:
        if _is_about(ticker, headline, body, index):
            continue
        wrong.append(article_id)
        report.by_source[source] = report.by_source.get(source, 0) + 1
        report.by_ticker[ticker] = report.by_ticker.get(ticker, 0) + 1
        if len(report.samples) < limit:
            report.samples.append(
                {"ticker": ticker, "source": source, "headline": headline}
            )

    report.misattributed = len(wrong)

    if apply and wrong:
        await db.execute(delete(NewsArticle).where(NewsArticle.id.in_(wrong)))
        await db.commit()
        report.deleted = len(wrong)
        logger.info("Deleted %d misattributed articles", report.deleted)

    return report
