"""SEC EDGAR filing ingestion.

Uses the modern JSON endpoints on data.sec.gov rather than scraping the legacy
browse-edgar HTML, which is both faster and far less brittle:

* ``https://www.sec.gov/files/company_tickers.json`` — ticker to CIK map
* ``https://data.sec.gov/submissions/CIK##########.json`` — recent filings

The SEC requires a descriptive User-Agent with a contact address and asks for no
more than 10 requests/second. Both are enforced here.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models import Stock
from app.services.ingest import IngestReport, RawArticle, store_articles

logger = logging.getLogger(__name__)

TICKER_MAP_URL = "https://www.sec.gov/files/company_tickers.json"
SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik}.json"
ARCHIVE_URL = "https://www.sec.gov/Archives/edgar/data/{cik}/{accession}/{document}"

# Filing types worth scoring. 10-K/10-Q are periodic results; 8-K carries the
# material events (approvals, trial data, M&A) that actually move these stocks.
INTERESTING_FORMS = {"8-K", "10-K", "10-Q", "6-K", "20-F", "S-1", "424B4"}

SOURCE = "sec_edgar"
REQUEST_DELAY_SECONDS = 0.15  # keeps us under the SEC's 10 req/s ceiling


def _headers() -> dict[str, str]:
    return {
        "User-Agent": get_settings().sec_user_agent,
        "Accept-Encoding": "gzip, deflate",
        "Host": "data.sec.gov",
    }


async def fetch_ticker_cik_map(client: httpx.AsyncClient) -> dict[str, str]:
    """Return ``{TICKER: zero-padded CIK}`` for every SEC registrant."""
    headers = _headers() | {"Host": "www.sec.gov"}
    response = await client.get(TICKER_MAP_URL, headers=headers, timeout=30.0)
    response.raise_for_status()

    mapping: dict[str, str] = {}
    for entry in response.json().values():
        ticker = str(entry.get("ticker", "")).upper()
        cik = entry.get("cik_str")
        if ticker and cik is not None:
            mapping[ticker] = str(cik).zfill(10)
    logger.info("Loaded %d ticker-to-CIK mappings from SEC", len(mapping))
    return mapping


async def fetch_sec_filings(
    client: httpx.AsyncClient, ticker: str, cik: str, limit: int = 10
) -> list[RawArticle]:
    """Fetch a company's most recent filings as ``RawArticle`` items."""
    url = SUBMISSIONS_URL.format(cik=cik)
    try:
        response = await client.get(url, headers=_headers(), timeout=30.0)
        response.raise_for_status()
        payload = response.json()
    except httpx.HTTPStatusError as exc:
        logger.warning("SEC returned %s for %s (CIK %s)", exc.response.status_code, ticker, cik)
        return []
    except (httpx.HTTPError, ValueError) as exc:
        logger.warning("SEC request failed for %s: %s", ticker, exc)
        return []

    recent = payload.get("filings", {}).get("recent", {})
    forms = recent.get("form", [])
    if not forms:
        return []

    accessions = recent.get("accessionNumber", [])
    dates = recent.get("filingDate", [])
    documents = recent.get("primaryDocument", [])
    descriptions = recent.get("primaryDocDescription", [])
    items = recent.get("items", [])
    company = payload.get("name", ticker)

    articles: list[RawArticle] = []
    for index, form in enumerate(forms):
        if len(articles) >= limit:
            break
        if form not in INTERESTING_FORMS:
            continue

        try:
            filed_on = datetime.strptime(dates[index], "%Y-%m-%d").replace(tzinfo=timezone.utc)
        except (IndexError, ValueError):
            logger.debug("Skipping %s filing with unparsable date", ticker)
            continue

        accession = accessions[index].replace("-", "") if index < len(accessions) else ""
        document = documents[index] if index < len(documents) else ""
        if not accession or not document:
            continue

        description = descriptions[index] if index < len(descriptions) else ""
        item_codes = items[index] if index < len(items) else ""

        articles.append(
            RawArticle(
                ticker=ticker,
                headline=f"{company} filed {form}"
                + (f": {description}" if description else ""),
                body=f"Form {form} filed {dates[index]}."
                + (f" Reported items: {item_codes}." if item_codes else ""),
                url=ARCHIVE_URL.format(
                    cik=str(int(cik)), accession=accession, document=document
                ),
                source=SOURCE,
                published_at=filed_on,
            )
        )

    return articles


async def _resolve_ciks(db: AsyncSession, stocks: list[Stock], client: httpx.AsyncClient) -> None:
    """Backfill missing CIKs on the given stocks from the SEC's ticker map."""
    missing = [stock for stock in stocks if not stock.cik]
    if not missing:
        return

    try:
        mapping = await fetch_ticker_cik_map(client)
    except (httpx.HTTPError, ValueError) as exc:
        logger.warning("Could not load SEC ticker map: %s", exc)
        return

    resolved = 0
    for stock in missing:
        cik = mapping.get(stock.ticker.upper())
        if cik:
            stock.cik = cik
            resolved += 1
    if resolved:
        await db.commit()
        logger.info("Resolved CIKs for %d stocks", resolved)


async def ingest_sec_filings(
    db: AsyncSession, tickers: list[str] | None = None, limit_per_ticker: int = 10
) -> IngestReport:
    """Fetch and store recent filings for the given tickers (default: all active)."""
    query = select(Stock).where(Stock.is_active.is_(True))
    if tickers:
        query = query.where(Stock.ticker.in_([t.upper() for t in tickers]))
    stocks = list((await db.execute(query)).scalars())

    if not stocks:
        logger.info("SEC ingest: no matching stocks")
        return IngestReport()

    async with httpx.AsyncClient(follow_redirects=True) as client:
        await _resolve_ciks(db, stocks, client)

        collected: list[RawArticle] = []
        for stock in stocks:
            if not stock.cik:
                logger.debug("No CIK for %s; skipping", stock.ticker)
                continue
            collected.extend(
                await fetch_sec_filings(client, stock.ticker, stock.cik, limit_per_ticker)
            )
            await asyncio.sleep(REQUEST_DELAY_SECONDS)

    return await store_articles(db, collected)
