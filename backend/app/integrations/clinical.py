"""Clinical and non-US regulatory sources.

Everything else here is either US-registrant filings or financial-press
coverage. These are the primary records behind the events that actually move a
pharma name, and the only sources covering companies that do not file with the
SEC:

* **ClinicalTrials.gov (API v2)** — trial status changes. A phase 3 moving to
  "Completed", or worse to "Terminated", is a leading indicator: it is on the
  registry before it is in a press release.
* **EMA** — European approvals and CHMP opinions. A positive CHMP opinion is
  the single largest catalyst for a European pharma name and is invisible to
  every other source wired up here.
* **RNS (London), TDnet (Tokyo), HKEX** — home-regulator filings for the
  `.L`, `.T` and `.HK` listings, which SEC EDGAR structurally cannot cover.

**Verification status, stated plainly.** This sandbox blocks all of these
hosts, so none of the response shapes below were confirmed against a live
response — they are written from the documented formats. ClinicalTrials.gov v2
and the EMA feed are stable and public. The exchange feeds are the shakiest:
LSE and HKEX do not publish a documented public RSS endpoint, so those are
included behind a setting that is **off by default** and will simply log a
failure and return nothing if the URL is wrong. Turn them on only after
checking the response by hand.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.services.feeds import fetch_feed, parse_datetime, strip_html
from app.services.ingest import IngestReport, RawArticle, store_articles
from app.services.matching import CompanyIndex, build_index, match_tickers

logger = logging.getLogger(__name__)

CLINICAL_TRIALS_URL = "https://clinicaltrials.gov/api/v2/studies"
EMA_FEED = "https://www.ema.europa.eu/en/rss/news.xml"

SOURCE_TRIALS = "clinicaltrials"
SOURCE_EMA = "ema"
SOURCE_EXCHANGE = "exchange_filing"

# Status changes worth a headline. "Recruiting" churns constantly and says
# nothing; these are the transitions that resolve uncertainty.
NOTABLE_STATUSES: dict[str, str] = {
    "COMPLETED": "completed",
    "TERMINATED": "terminated early",
    "SUSPENDED": "suspended",
    "WITHDRAWN": "withdrawn before enrolment",
    "ACTIVE_NOT_RECRUITING": "closed to enrolment, still active",
}


def parse_studies(payload: dict, index: CompanyIndex) -> list[RawArticle]:
    """Convert a ClinicalTrials.gov v2 response into articles."""
    articles: list[RawArticle] = []

    for study in payload.get("studies") or []:
        section = study.get("protocolSection") or {}
        identification = section.get("identificationModule") or {}
        status_module = section.get("statusModule") or {}
        sponsor_module = section.get("sponsorCollaboratorsModule") or {}
        design = section.get("designModule") or {}

        nct_id = identification.get("nctId")
        title = identification.get("briefTitle")
        status = (status_module.get("overallStatus") or "").upper()
        if not nct_id or not title or status not in NOTABLE_STATUSES:
            continue

        sponsor = ((sponsor_module.get("leadSponsor") or {}).get("name") or "").strip()
        # Match on the sponsor only. A trial title names the drug and the
        # indication, and a competitor's compound in it would misattribute.
        tickers = match_tickers(sponsor, index, limit=1)
        if not tickers:
            continue

        phases = ", ".join(design.get("phases") or []) or "trial"
        updated = (
            (status_module.get("lastUpdatePostDateStruct") or {}).get("date")
            or (status_module.get("statusVerifiedDate") or "")
        )
        published = parse_datetime(updated) or datetime.now(timezone.utc)

        articles.append(
            RawArticle(
                ticker=tickers[0],
                headline=f"{sponsor} {phases} {NOTABLE_STATUSES[status]}: {title}",
                body=None,
                url=f"https://clinicaltrials.gov/study/{nct_id}",
                source=SOURCE_TRIALS,
                published_at=published,
            )
        )
    return articles


async def fetch_studies(client: httpx.AsyncClient, since: datetime, limit: int) -> dict:
    """Query ClinicalTrials.gov for recently updated studies."""
    try:
        response = await client.get(
            CLINICAL_TRIALS_URL,
            params={
                "filter.advanced": f"AREA[LastUpdatePostDate]RANGE[{since:%Y-%m-%d},MAX]",
                "pageSize": limit,
                "format": "json",
            },
            headers={"Accept": "application/json"},
            timeout=30.0,
        )
    except httpx.HTTPError as exc:
        logger.warning("ClinicalTrials.gov request failed: %s", exc)
        return {}

    if response.status_code != 200:
        logger.warning("ClinicalTrials.gov returned HTTP %s", response.status_code)
        return {}
    try:
        return response.json()
    except ValueError:
        return {}


async def ingest_clinical_and_regulatory(
    db: AsyncSession, lookback_days: int | None = None
) -> IngestReport:
    """Pull trial status changes, EMA news, and (optionally) exchange filings."""
    settings = get_settings()
    if not settings.clinical_enabled:
        logger.info("Clinical ingest skipped: CLINICAL_ENABLED is false")
        return IngestReport()

    index = await build_index(db)
    if not index:
        return IngestReport()

    days = lookback_days or settings.clinical_lookback_days
    since = datetime.now(timezone.utc) - timedelta(days=days)
    collected: list[RawArticle] = []

    async with httpx.AsyncClient() as client:
        payload = await fetch_studies(client, since, settings.clinical_batch_size)
        collected.extend(parse_studies(payload, index))

        ema_entries = (
            await fetch_feed(client, settings.ema_feed) if settings.ema_feed else []
        )
        for entry in ema_entries:
            if entry.published_at < since:
                continue
            tickers = match_tickers(entry.title, index, limit=2)
            for ticker in tickers:
                collected.append(
                    RawArticle(
                        ticker=ticker,
                        headline=entry.title,
                        body=strip_html(entry.summary),
                        url=entry.link,
                        source=SOURCE_EMA,
                        published_at=entry.published_at,
                    )
                )

        # Off by default: these endpoints are unverified, so an operator opts
        # in after checking them rather than discovering the failure in a log.
        for feed_url in settings.exchange_filing_feeds:
            for entry in await fetch_feed(client, feed_url):
                if entry.published_at < since:
                    continue
                tickers = match_tickers(entry.title, index, limit=2)
                for ticker in tickers:
                    collected.append(
                        RawArticle(
                            ticker=ticker,
                            headline=entry.title,
                            body=strip_html(entry.summary),
                            url=entry.link,
                            source=SOURCE_EXCHANGE,
                            published_at=entry.published_at,
                        )
                    )

    if not collected:
        return IngestReport()

    report = await store_articles(db, collected)
    logger.info("Clinical/regulatory ingest: %s", report.as_dict())
    return report
