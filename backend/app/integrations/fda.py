"""FDA: approvals, recalls, enforcement and press announcements.

The single most on-thesis source this platform was missing. For a pharma
watchlist an FDA decision is usually *the* event, and it reaches openFDA and
the FDA newsroom directly rather than by way of a financial vendor.

Two channels, because they carry different things:

* **openFDA** — structured records. Drug enforcement (recalls, classified by
  hazard) and device recalls, with the recalling firm named in a field rather
  than buried in prose.
* **FDA press announcements** — the newsroom feed, where approvals and safety
  communications appear. Prose, so the company has to be recognised in it.

Neither needs an API key. openFDA rate-limits anonymous callers to a few
requests a minute, which is ample for a job that runs every 15 minutes.
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta, timezone

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.services.feeds import fetch_feed, strip_html
from app.services.ingest import IngestReport, RawArticle, store_articles
from app.services.matching import CompanyIndex, build_index, match_tickers

logger = logging.getLogger(__name__)

OPENFDA_URL = "https://api.fda.gov"
PRESS_FEED = "https://www.fda.gov/about-fda/contact-fda/stay-informed/rss-feeds/press-releases/rss.xml"
SOURCE = "fda"

# Class I is "reasonable probability of serious adverse health consequences or
# death" — the classification that moves a share price. Kept in the headline
# so the lexicon and the reader both see it.
_CLASS_URGENCY = {
    "Class I": "Class I (most serious)",
    "Class II": "Class II",
    "Class III": "Class III (least serious)",
}


def _recall_headline(record: dict) -> str | None:
    """Turn an enforcement record into a sentence worth scoring."""
    firm = (record.get("recalling_firm") or "").strip()
    product = (record.get("product_description") or "").strip()
    if not firm or not product:
        return None

    classification = _CLASS_URGENCY.get(
        (record.get("classification") or "").strip(),
        (record.get("classification") or "").strip(),
    )
    reason = (record.get("reason_for_recall") or "").strip()

    headline = f"{firm} recalls {product[:160]}"
    if classification:
        headline += f" — {classification}"
    if reason:
        headline += f": {reason[:160]}"
    return " ".join(headline.split())


def _recall_date(record: dict) -> datetime:
    """Prefer the report date; fall back to today rather than dropping the row."""
    for field in ("report_date", "recall_initiation_date", "center_classification_date"):
        raw = record.get(field)
        if raw and len(str(raw)) == 8:
            try:
                return datetime.strptime(str(raw), "%Y%m%d").replace(tzinfo=timezone.utc)
            except ValueError:
                continue
    return datetime.now(timezone.utc)


def parse_enforcement(payload: dict, index: CompanyIndex, endpoint: str) -> list[RawArticle]:
    """Convert an openFDA enforcement response into articles."""
    articles: list[RawArticle] = []
    for record in payload.get("results") or []:
        headline = _recall_headline(record)
        if not headline:
            continue

        # The recalling firm is a field, so match on it alone rather than on
        # the whole record — a product description mentioning another brand
        # would otherwise attribute the recall to the wrong company.
        tickers = match_tickers(record.get("recalling_firm") or "", index, limit=1)
        if not tickers:
            continue

        # Enforcement reports have no canonical URL; the recall number is the
        # stable identifier and keeps the (url, source) dedup key meaningful.
        recall_number = (record.get("recall_number") or "").strip()
        if not recall_number:
            continue

        articles.append(
            RawArticle(
                ticker=tickers[0],
                headline=headline,
                body=strip_html(record.get("reason_for_recall")),
                url=f"https://api.fda.gov/{endpoint}?search=recall_number:{recall_number}",
                source=SOURCE,
                published_at=_recall_date(record),
            )
        )
    return articles


async def fetch_enforcement(
    client: httpx.AsyncClient, endpoint: str, since: date, limit: int
) -> dict:
    """Fetch one openFDA enforcement endpoint. Returns {} on any failure."""
    try:
        response = await client.get(
            f"{OPENFDA_URL}/{endpoint}",
            params={
                "search": f"report_date:[{since:%Y%m%d}+TO+{date.today():%Y%m%d}]",
                "limit": limit,
            },
            timeout=30.0,
        )
    except httpx.HTTPError as exc:
        logger.warning("openFDA request failed for %s: %s", endpoint, exc)
        return {}

    # openFDA answers 404 when a search matches nothing. That is an empty
    # result, not an error, and must not be logged as one every quiet day.
    if response.status_code == 404:
        return {}
    if response.status_code == 429:
        logger.warning("openFDA rate-limited on %s", endpoint)
        return {}
    if response.status_code != 200:
        logger.warning("openFDA returned HTTP %s for %s", response.status_code, endpoint)
        return {}

    try:
        return response.json()
    except ValueError:
        return {}


async def ingest_fda(db: AsyncSession, lookback_days: int | None = None) -> IngestReport:
    """Pull recalls, enforcement reports and press announcements."""
    settings = get_settings()
    if not settings.fda_enabled:
        logger.info("FDA ingest skipped: FDA_ENABLED is false")
        return IngestReport()

    index = await build_index(db)
    if not index:
        return IngestReport()

    days = lookback_days or settings.fda_lookback_days
    since = date.today() - timedelta(days=days)
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    collected: list[RawArticle] = []

    async with httpx.AsyncClient() as client:
        for endpoint in ("drug/enforcement.json", "device/enforcement.json"):
            payload = await fetch_enforcement(client, endpoint, since, settings.fda_batch_size)
            collected.extend(parse_enforcement(payload, index, endpoint))

        # Skipped when unset, so the structured enforcement data above still
        # runs without a working newsroom URL.
        press_entries = (
            await fetch_feed(client, settings.fda_press_feed)
            if settings.fda_press_feed
            else []
        )
        for entry in press_entries:
            if entry.published_at < cutoff:
                continue
            # Press releases name the company in the title or the summary;
            # both are searched, the title first because it is less noisy.
            tickers = match_tickers(entry.title, index, limit=2) or match_tickers(
                strip_html(entry.summary) or "", index, limit=2
            )
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

    if not collected:
        return IngestReport()

    report = await store_articles(db, collected)
    logger.info("FDA ingest: %s", report.as_dict())
    return report
