"""One question, answered from three sources: what is worth looking at now.

The dashboard could already show scored news, live setups and a forward
calendar, and answering "which stocks have good news and a trade in them"
meant reading three screens and intersecting them by eye. That intersection is
the actual question, and doing it by hand is both tedious and where the
mistakes come from — a headline noticed without checking whether the chart
offers an entry, or a setup taken without noticing earnings tomorrow.

What this deliberately does **not** do is predict. A row here means a positive
story landed and a setup is live with a defined stop, which is a reason to
look. Nothing in this platform knows what a price will do today, and the
response says so rather than letting a ranked list imply it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import CatalystEvent, NewsArticle, SentimentScore, Stock

# Strong enough to be a catalyst rather than mildly good-sounding prose. The
# lexicon saturates a one-sided headline, so this is really "at least one
# clearly positive term and nothing pulling the other way".
DEFAULT_MIN_SCORE = 0.5

# A day. Long enough to catch a release that landed after yesterday's close —
# which is when most of them land — and short enough that the news is still
# what the tape is reacting to.
DEFAULT_HOURS = 24

# How far ahead a scheduled event still colours today's trade. An earnings date
# tomorrow changes the risk of holding a position opened this morning.
CATALYST_DAYS = 5


@dataclass
class NewsSummary:
    """The positive news behind one symbol, condensed."""

    articles: int = 0
    best_score: float = 0.0
    corroborations: int = 0
    latest_at: datetime | None = None
    headlines: list[dict] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "articles": self.articles,
            "best_score": round(self.best_score, 3),
            # Several wires carrying one release within minutes is the
            # difference between a company announcement and an aggregator.
            "corroborations": self.corroborations,
            "latest_at": self.latest_at.isoformat() if self.latest_at else None,
            "headlines": self.headlines,
        }


async def positive_news(
    db: AsyncSession,
    hours: int = DEFAULT_HOURS,
    min_score: float = DEFAULT_MIN_SCORE,
    per_symbol: int = 3,
) -> dict[str, NewsSummary]:
    """Recent strongly-positive news, grouped by symbol.

    Duplicates are excluded, as everywhere else: one release carried by four
    wires is one event, and counting it four times would put the loudest press
    office at the top of the list rather than the best news.
    """
    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
    rows = (
        await db.execute(
            select(NewsArticle, SentimentScore, Stock.ticker)
            .join(SentimentScore, SentimentScore.article_id == NewsArticle.id)
            .join(Stock, Stock.id == NewsArticle.ticker_id)
            .where(
                NewsArticle.published_at >= cutoff,
                NewsArticle.duplicate_of_id.is_(None),
                SentimentScore.score >= min_score,
            )
            .order_by(NewsArticle.published_at.desc())
        )
    ).all()

    # How many other wires carried each of these stories. Counted in one
    # query rather than per article: the field is the difference between a
    # company announcement and a single aggregator picking something up, and
    # left uncounted it would read as "nobody else carried this" for every row.
    primary_ids = [article.id for article, _, _ in rows]
    echoes: dict[int, int] = {}
    if primary_ids:
        echoes = dict(
            (
                await db.execute(
                    select(NewsArticle.duplicate_of_id, func.count())
                    .where(NewsArticle.duplicate_of_id.in_(primary_ids))
                    .group_by(NewsArticle.duplicate_of_id)
                )
            ).all()
        )

    by_symbol: dict[str, NewsSummary] = {}
    for article, score, ticker in rows:
        summary = by_symbol.setdefault(ticker.upper(), NewsSummary())
        summary.articles += 1
        summary.corroborations = max(summary.corroborations, echoes.get(article.id, 0))
        summary.best_score = max(summary.best_score, score.score)
        published = _aware(article.published_at)
        if summary.latest_at is None or published > summary.latest_at:
            summary.latest_at = published
        if len(summary.headlines) < per_symbol:
            summary.headlines.append(
                {
                    "headline": article.headline,
                    "score": round(score.score, 3),
                    "event_type": score.event_type,
                    "published_at": published.isoformat(),
                    "source": article.source,
                    "corroborations": echoes.get(article.id, 0),
                    # Only a real URL. The old feed parser stored some GUIDs
                    # in this column and they are not openable.
                    "url": article.url if str(article.url).startswith("http") else None,
                }
            )
    return by_symbol


async def upcoming_catalysts(
    db: AsyncSession, days: int = CATALYST_DAYS
) -> dict[str, list[dict]]:
    """Scheduled events per symbol inside the horizon.

    Carried because it cuts both ways and only the trader can say which: an
    earnings date tomorrow is a reason to size down or to stay flat overnight,
    not a reason the setup is better.
    """
    now = datetime.now(timezone.utc)
    rows = (
        await db.execute(
            select(CatalystEvent, Stock.ticker)
            .join(Stock, Stock.id == CatalystEvent.ticker_id)
            .where(
                CatalystEvent.expected_at >= now,
                CatalystEvent.expected_at <= now + timedelta(days=days),
            )
            .order_by(CatalystEvent.expected_at)
        )
    ).all()

    by_symbol: dict[str, list[dict]] = {}
    for event, ticker in rows:
        by_symbol.setdefault(ticker.upper(), []).append(
            {
                "kind": event.kind,
                "expected_at": _aware(event.expected_at).isoformat(),
                "days_away": max(0, (_aware(event.expected_at).date() - now.date()).days),
                "confidence": event.confidence,
                "title": event.title,
            }
        )
    return by_symbol


def combine(
    signals: list[dict],
    news: dict[str, NewsSummary],
    catalysts: dict[str, list[dict]],
    names: dict[str, str],
    direction: str | None = "long",
    require_actionable: bool = True,
) -> list[dict]:
    """Join live setups to the news and the calendar, best news first.

    Sorted by the news rather than by the setup, because the setups carry no
    measured hit rate to rank them by — they are conditions, and ordering them
    would invent a preference the data does not support. The news score is at
    least a measured quantity.
    """
    rows: list[dict] = []
    for signal in signals:
        ticker = signal["ticker"].upper()
        summary = news.get(ticker)
        if summary is None:
            continue
        if direction and signal.get("direction") != direction:
            continue
        if require_actionable and not signal.get("actionable", False):
            continue

        rows.append(
            {
                "ticker": ticker,
                "company_name": names.get(ticker),
                "setup": {
                    key: signal.get(key)
                    for key in (
                        "setup",
                        "direction",
                        "entry",
                        "stop",
                        "target",
                        "reward_risk",
                        "risk_per_share",
                        "position",
                        "bars_minutes_old",
                        "actionable",
                    )
                    if key in signal
                },
                "news": summary.as_dict(),
                "catalysts": catalysts.get(ticker, []),
            }
        )

    rows.sort(
        key=lambda row: (
            -row["news"]["best_score"],
            -row["news"]["corroborations"],
            -row["news"]["articles"],
            row["ticker"],
        )
    )
    return rows


def _aware(moment: datetime) -> datetime:
    return moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)
