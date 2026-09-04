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
from app.services.filing_text import TARGET_ITEM, prepare
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

# An 8-K's meaning lives in its item codes. The submissions feed gives them as
# bare numbers ("8.01,9.01"), which carry no words for the sentiment scorer or
# the event classifier to read — so every filing used to land as
# neutral/other. Expanding them to their official titles turns the SEC into a
# real signal source: "Results of Operations" reaches the earnings patterns,
# "Completion of Acquisition" the M&A ones, "Bankruptcy" the negative lexicon.
EIGHT_K_ITEMS: dict[str, str] = {
    "1.01": "Entry into a Material Definitive Agreement",
    "1.02": "Termination of a Material Definitive Agreement",
    "1.03": "Bankruptcy or Receivership",
    "1.05": "Material Cybersecurity Incident",
    "2.01": "Completion of Acquisition or Disposition of Assets",
    "2.02": "Results of Operations and Financial Condition",
    "2.03": "Creation of a Direct Financial Obligation",
    "2.04": "Triggering Events That Accelerate a Financial Obligation",
    "2.05": "Costs Associated with Exit or Disposal Activities",
    "2.06": "Material Impairments",
    "3.01": "Notice of Delisting or Failure to Satisfy a Listing Rule",
    "3.02": "Unregistered Sales of Equity Securities",
    "3.03": "Material Modification to Rights of Security Holders",
    "4.01": "Changes in Registrant's Certifying Accountant",
    "4.02": "Non-Reliance on Previously Issued Financial Statements",
    "5.01": "Changes in Control of Registrant",
    "5.02": "Departure or Election of Directors or Principal Officers",
    "5.03": "Amendments to Articles of Incorporation or Bylaws",
    "5.07": "Submission of Matters to a Vote of Security Holders",
    "7.01": "Regulation FD Disclosure",
    "8.01": "Other Events",
    "9.01": "Financial Statements and Exhibits",
}

# Forms whose very type is the signal, for the same reason.
FORM_DESCRIPTIONS: dict[str, str] = {
    "8-K": "current report on a material event",
    "10-K": "annual report including full-year results",
    "10-Q": "quarterly report including quarterly results",
    "6-K": "foreign private issuer report of a material event",
    "20-F": "foreign private issuer annual report",
    "S-1": "registration statement for a securities offering",
    "424B4": "prospectus for a completed securities offering",
}


def describe_items(item_codes: str) -> list[str]:
    """Expand ``"8.01,9.01"`` into the official item titles, in order."""
    described: list[str] = []
    for raw in item_codes.split(","):
        code = raw.strip()
        if not code:
            continue
        # The feed sometimes prefixes the form, e.g. "Item 8.01".
        code = code.replace("Item", "").strip()
        title = EIGHT_K_ITEMS.get(code)
        described.append(f"Item {code}: {title}" if title else f"Item {code}")
    return described


def _headers() -> dict[str, str]:
    """Headers for any SEC request, on either host.

    Host is deliberately absent. httpx derives it from the URL, and pinning it
    here sent ``Host: data.sec.gov`` with requests to www.sec.gov/Archives —
    which the SEC routes by Host, so every filing document came back 404 while
    the submissions feed on data.sec.gov worked perfectly. That silently broke
    all three Archives callers: filing text at ingest, the filing-text
    backfill, and Form 4 insider parsing. Only the ticker map noticed, and it
    worked around it locally instead of fixing it here.
    """
    return {
        "User-Agent": get_settings().sec_user_agent,
        "Accept-Encoding": "gzip, deflate",
    }


async def fetch_ticker_cik_map(client: httpx.AsyncClient) -> dict[str, str]:
    """Return ``{TICKER: zero-padded CIK}`` for every SEC registrant."""
    response = await client.get(TICKER_MAP_URL, headers=_headers(), timeout=30.0)
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
    client: httpx.AsyncClient,
    ticker: str,
    cik: str,
    limit: int = 10,
    read_text: bool = True,
) -> list[RawArticle]:
    """Fetch a company's most recent filings as ``RawArticle`` items.

    ``read_text`` fetches the document itself for 8-Ks reporting Item 8.01 and
    uses its narrative as the article body. Without it the body is the item
    code expanded to its title — "Other Events" — which is what the filing is
    filed under rather than what it says, and which scores exactly zero.
    One extra request per qualifying filing, and only for that one item type.
    """
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

        # Put the most meaningful words in the headline, since that is what the
        # feed shows and what the scorer weighs first.
        items_described = describe_items(item_codes) if item_codes else []
        # The feed often sets primaryDocDescription to the form name itself, and
        # "filed 8-K: 8-K" tells a reader nothing — prefer the item title then.
        if description.strip().upper() == form.upper():
            description = ""
        headline_detail = description or (
            items_described[0].split(": ", 1)[-1] if items_described else ""
        )
        body_parts = [
            f"Form {form} filed {dates[index]} "
            f"({FORM_DESCRIPTIONS.get(form, 'SEC filing')})."
        ]
        if items_described:
            body_parts.append("Reported items: " + "; ".join(items_described) + ".")
        if description and description not in headline_detail:
            body_parts.append(description)

        body = " ".join(body_parts)
        # The narrative, where there is one. An 8-K's item code says which
        # drawer it was filed in; the document says the trial met its endpoint.
        if read_text and form == "8-K" and TARGET_ITEM in (item_codes or ""):
            narrative = await _read_filing_text(client, cik, accession, document)
            if narrative:
                body = f"{body} {narrative}"

        articles.append(
            RawArticle(
                ticker=ticker,
                headline=f"{company} filed {form}"
                + (f": {headline_detail}" if headline_detail else ""),
                body=body,
                url=ARCHIVE_URL.format(
                    cik=str(int(cik)), accession=accession, document=document
                ),
                source=SOURCE,
                published_at=filed_on,
            )
        )

    return articles


async def _read_filing_text(
    client: httpx.AsyncClient, cik: str, accession: str, document: str
) -> str | None:
    """The Item 8.01 narrative from one filing, or None.

    Every failure is soft. A filing whose document cannot be fetched or parsed
    still produces an article from its metadata, which is what the platform had
    before this existed — a missing body loses signal, a raised exception loses
    the whole ingest.
    """
    url = ARCHIVE_URL.format(cik=str(int(cik)), accession=accession, document=document)
    try:
        response = await client.get(url, headers=_headers(), timeout=30.0)
        response.raise_for_status()
    except httpx.HTTPError as exc:
        logger.debug("Could not read filing text at %s: %s", url, exc)
        return None
    try:
        return prepare(response.text)
    except Exception:  # noqa: BLE001 - filings are malformed in creative ways
        logger.debug("Could not parse filing text at %s", url)
        return None


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
