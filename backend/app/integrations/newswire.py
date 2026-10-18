"""Newswire feeds: the earliest copy of a press release.

A company's own release reaches GlobeNewswire, Business Wire and PR Newswire
at issue, before any aggregator has picked it up and often before the stock
has moved. For a catalyst-driven watchlist that is the difference between
reading the news and reacting to it.

The cost is precision. These feeds carry every release from every issuer in a
category, and the company is named in prose rather than in a field, so the
ticker has to be inferred. Two defences:

* **Matching is on the headline, not the body.** A release body routinely
  names partners, acquirers, trial sites and index memberships; matching on it
  attaches the story to companies it merely mentions.
* **At most two tickers per item.** A release naming several companies is a
  partnership or an index note, and beyond two it is a market round-up whose
  sentiment belongs to nobody.

This is the noisiest source wired up here, and the one to disable first if the
feed starts filling with stories that are not about your names.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.services.feeds import fetch_feed, strip_html
from app.services.ingest import IngestReport, RawArticle, store_articles
from app.services.matching import build_index, match_tickers

logger = logging.getLogger(__name__)

SOURCE = "newswire"


@dataclass(frozen=True)
class Wire:
    """One newswire feed and the label its stories carry."""

    key: str
    name: str
    url: str


# Health and technology categories only. The general business feeds carry
# thousands of releases a day, almost none of them about this universe.
# GlobeNewswire is deliberately absent: both of its category feeds read-timeout
# rather than answering, and a source that reliably times out costs a request
# slot every cycle and returns nothing. Add it back through NEWSWIRE_FEEDS if it
# answers for you.
WIRES: tuple[Wire, ...] = (
    Wire(
        key="businesswire_health",
        name="Business Wire Health",
        url="https://feed.businesswire.com/rss/home/?rss=G1QFDERJXkJeEFpRWQ==",
    ),
    Wire(
        key="prnewswire_health",
        name="PR Newswire Health",
        url="https://www.prnewswire.com/rss/health-latest-news/health-latest-news-list.rss",
    ),
    Wire(
        key="prnewswire_tech",
        name="PR Newswire Technology",
        url="https://www.prnewswire.com/rss/technology-latest-news/technology-latest-news-list.rss",
    ),
)


async def ingest_newswires(
    db: AsyncSession,
    lookback_hours: int | None = None,
    wires: tuple[Wire, ...] | None = None,
) -> IngestReport:
    """Read every configured wire and store items naming a tracked company."""
    settings = get_settings()
    if not settings.newswire_enabled:
        logger.info("Newswire ingest skipped: NEWSWIRE_ENABLED is false")
        return IngestReport()

    index = await build_index(db)
    if not index:
        return IngestReport()

    cutoff = datetime.now(timezone.utc) - timedelta(
        hours=lookback_hours or settings.newswire_lookback_hours
    )
    if wires is None and settings.newswire_feeds:
        # An operator who found which wires answer for them keeps only those.
        wires = tuple(
            Wire(key=f"custom_{i}", name=url, url=url)
            for i, url in enumerate(settings.newswire_feeds)
        )
    collected: list[RawArticle] = []
    seen_links: set[str] = set()

    async with httpx.AsyncClient() as client:
        for wire in wires or WIRES:
            entries = await fetch_feed(client, wire.url)
            matched = 0

            for entry in entries:
                if entry.published_at < cutoff or entry.link in seen_links:
                    continue

                tickers = match_tickers(entry.title, index, limit=2)
                if not tickers:
                    continue

                seen_links.add(entry.link)
                matched += 1
                for ticker in tickers:
                    collected.append(
                        RawArticle(
                            ticker=ticker,
                            headline=entry.title,
                            body=strip_html(entry.summary),
                            url=entry.link,
                            source=SOURCE,
                            published_at=entry.published_at,
                        )
                    )

            logger.debug("%s: %d of %d entries matched", wire.name, matched, len(entries))

    if not collected:
        return IngestReport()

    report = await store_articles(db, collected)
    logger.info("Newswire ingest: %s", report.as_dict())
    return report
