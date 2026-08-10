"""SEC EDGAR's current-filings feed: every registrant, ~1 minute behind.

The existing SEC integration walks the universe company by company on a
30-minute cycle, so an 8-K filed at 09:01 can wait until 09:30. EDGAR also
publishes a single feed of filings as they are accepted, across all
registrants. Reading that turns a 30-minute worst case into roughly one
minute, for one request instead of 163, and with no new vendor.

The two are complements, not alternatives. This catches new filings quickly
but only looks at the recent window; the per-company walk still backfills
history and covers anything the feed dropped while the process was down.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta, timezone

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.integrations.sec import FORM_DESCRIPTIONS
from app.services.feeds import fetch_feed, strip_html
from app.services.ingest import IngestReport, RawArticle, store_articles
from app.services.matching import build_index, match_tickers

logger = logging.getLogger(__name__)

FEED_URL = "https://www.sec.gov/cgi-bin/browse-edgar"
SOURCE = "sec_edgar"

# Forms worth waking up for. A 4 (insider transaction) or an ownership filing
# arrives constantly and moves nothing; these are the ones that carry news.
DEFAULT_FORMS: tuple[str, ...] = ("8-K", "6-K", "10-Q", "10-K", "SC 13D", "425")

# Entries read "8-K - PFIZER INC (0000078003) (Filer)". The company name is
# what has to be matched to a ticker, so it is lifted out of that shape.
# The separator is a spaced hyphen. A bare [^-]+ for the form breaks on every
# hyphenated form type there is — 8-K, 10-Q, 10-K — capturing "8" and leaving
# "K - PFIZER INC" as the company name, which then matches nothing.
_TITLE = re.compile(
    r"^(?P<form>.+?)\s+-\s+(?P<company>.+?)\s*\((?P<cik>\d{4,10})\)"
)


def parse_title(title: str) -> tuple[str | None, str | None]:
    """Split a feed title into (form type, company name)."""
    match = _TITLE.match(title.strip())
    if not match:
        return None, None
    return match.group("form").strip(), match.group("company").strip()


def to_headline(form: str, company: str) -> str:
    """Give the filing a headline the sentiment lexicon can read.

    "8-K" means nothing to a word-based scorer; the official title does. This
    is the same expansion the per-company path applies, kept identical so the
    two sources produce comparable text for one filing.
    """
    description = FORM_DESCRIPTIONS.get(form.upper())
    if description:
        return f"{company} filed {form}: {description}"
    return f"{company} filed {form}"


async def ingest_recent_filings(
    db: AsyncSession,
    forms: tuple[str, ...] | None = None,
    lookback_minutes: int | None = None,
) -> IngestReport:
    """Read the current-filings feed and store anything matching the universe."""
    settings = get_settings()
    forms = forms or DEFAULT_FORMS
    window = timedelta(minutes=lookback_minutes or settings.edgar_firehose_lookback_minutes)
    cutoff = datetime.now(timezone.utc) - window

    index = await build_index(db)
    if not index:
        logger.info("EDGAR firehose: no tracked stocks")
        return IngestReport()

    collected: list[RawArticle] = []
    async with httpx.AsyncClient() as client:
        for form in forms:
            entries = await fetch_feed(
                client,
                FEED_URL,
                user_agent=settings.sec_user_agent,
                params={
                    "action": "getcurrent",
                    "type": form,
                    "company": "",
                    "dateb": "",
                    "owner": "include",
                    "count": "100",
                    "output": "atom",
                },
            )

            for entry in entries:
                if entry.published_at < cutoff:
                    continue

                form_type, company = parse_title(entry.title)
                if not form_type or not company:
                    continue

                # Match on the company name only. The rest of the title is the
                # form type and CIK, and a bare number is not evidence.
                tickers = match_tickers(company, index, limit=1)
                if not tickers:
                    continue

                collected.append(
                    RawArticle(
                        ticker=tickers[0],
                        headline=to_headline(form_type, company),
                        body=strip_html(entry.summary),
                        url=entry.link,
                        source=SOURCE,
                        published_at=entry.published_at,
                    )
                )

    if not collected:
        logger.debug("EDGAR firehose: nothing new in the last %s", window)
        return IngestReport()

    report = await store_articles(db, collected)
    logger.info("EDGAR firehose: %s", report.as_dict())
    return report
