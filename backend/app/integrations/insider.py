"""Open-market insider transactions, from SEC Form 4.

The news firehose deliberately filters Form 4 out, and it is right to: a single
officer selling on a pre-arranged schedule is noise, and the feed carries
thousands of them a day. Aggregated, they are a different thing entirely — a
cluster of officers buying their own stock on the open market is among the
better-evidenced signals available, and it is information no news feed carries
because it is not news.

So these are not stored as articles. They are transactions, aggregated into a
factor, and reported like any other factor: computed, ranked in validate(), and
weightless until the measurement supports them.

**Only P and S are kept.** Form 4 codes cover grants (A), option exercises (M),
tax withholding (F) and gifts (G) as well as open-market purchases (P) and
sales (S). Only the last two are decisions about price; the rest are
compensation mechanics that would swamp them by volume.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from xml.etree import ElementTree

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.integrations.sec import SUBMISSIONS_URL, _headers
from app.models import InsiderTransaction, Stock

logger = logging.getLogger(__name__)

ARCHIVE_URL = "https://www.sec.gov/Archives/edgar/data/{cik}/{accession}/{document}"

# The SEC asks for no more than ten requests a second and means it. One filing
# costs one request, and a company's submissions index costs one more.
REQUEST_DELAY_SECONDS = 0.15

# Decisions about price. Everything else on a Form 4 is compensation mechanics.
OPEN_MARKET_CODES = frozenset({"P", "S"})

# How far back a filing is worth fetching. Form 4 is due within two business
# days, so anything older than this was already counted on a previous run.
DEFAULT_LOOKBACK_DAYS = 7


@dataclass
class InsiderReport:
    """What one Form 4 pass found."""

    symbols: int = 0
    filings_seen: int = 0
    filings_parsed: int = 0
    transactions_stored: int = 0
    skipped_not_open_market: int = 0
    note: str | None = None
    # Why a company's filing list could not be read, by HTTP status or
    # exception name. Without this, filings_seen: 0 reads as "nothing was
    # filed" whether or not anything was actually asked.
    lookup_failures: dict[str, int] = field(default_factory=dict)
    failed: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "symbols": self.symbols,
            "filings_seen": self.filings_seen,
            "filings_parsed": self.filings_parsed,
            "transactions_stored": self.transactions_stored,
            "skipped_not_open_market": self.skipped_not_open_market,
            "note": self.note,
            "lookup_failures": self.lookup_failures,
            "failed": self.failed,
        }


def raw_xml_document(document: str) -> str:
    """The machine-readable Form 4 behind a rendered one.

    ``primaryDocument`` usually points at the XSL-rendered view —
    ``xslF345X05/wk-form4_123.xml`` — which is HTML dressed as XML and parses
    into nothing useful. The source document sits in the same directory under
    the same name, so dropping the stylesheet segment is the whole conversion.
    """
    if "/" in document:
        return document.rsplit("/", 1)[1]
    return document


def _text(node, path: str) -> str | None:
    """A Form 4 field, which is nested one level deeper than it looks.

    Every value in this schema is wrapped: ``<transactionShares><value>100
    </value></transactionShares>``. Reading the outer element returns
    whitespace, which parses to zero and silently reports a trade of no shares.
    """
    found = node.find(path)
    if found is None:
        return None
    inner = found.find("value")
    target = inner if inner is not None else found
    return (target.text or "").strip() or None


def _number(node, path: str) -> float | None:
    raw = _text(node, path)
    if raw is None:
        return None
    try:
        return float(raw)
    except ValueError:
        return None


def parse_form4(xml: str) -> list[dict]:
    """Every open-market transaction in one Form 4.

    Derivative transactions are ignored. An option grant or an exercise is a
    compensation event, and its "price" is a strike set years ago rather than
    an opinion about today.
    """
    try:
        root = ElementTree.fromstring(xml)
    except ElementTree.ParseError:
        return []

    owner = root.find("reportingOwner")
    name = None
    title = None
    if owner is not None:
        name = _text(owner, "reportingOwnerId/rptOwnerName")
        relationship = owner.find("reportingOwnerRelationship")
        if relationship is not None:
            title = _text(relationship, "officerTitle")
            if title is None:
                # No officer title: fall back to the box that was ticked.
                flags = [
                    label
                    for tag, label in (
                        ("isDirector", "Director"),
                        ("isOfficer", "Officer"),
                        ("isTenPercentOwner", "10% owner"),
                    )
                    if (_text(relationship, tag) or "0") in {"1", "true"}
                ]
                title = ", ".join(flags) or None

    rows: list[dict] = []
    table = root.find("nonDerivativeTable")
    if table is None:
        return rows

    for sequence, transaction in enumerate(table.findall("nonDerivativeTransaction")):
        code = _text(transaction, "transactionCoding/transactionCode")
        if code is None:
            continue
        code = code.upper()

        shares = _number(transaction, "transactionAmounts/transactionShares")
        price = _number(transaction, "transactionAmounts/transactionPricePerShare")
        disposed = (
            _text(transaction, "transactionAmounts/transactionAcquiredDisposedCode")
            or "A"
        ).upper()
        traded = _text(transaction, "transactionDate")

        rows.append(
            {
                "sequence": sequence,
                "code": code,
                "open_market": code in OPEN_MARKET_CODES,
                "insider_name": name,
                "insider_title": title,
                "shares": shares,
                "price_per_share": price,
                # Signed at parse time, so summing a symbol's rows gives net
                # conviction without re-reading the acquired/disposed flag.
                "value": (
                    (shares or 0) * (price or 0) * (-1 if disposed == "D" else 1)
                    if shares is not None and price is not None
                    else None
                ),
                "traded_on": _parse_date(traded),
            }
        )
    return rows


def _parse_date(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return datetime.strptime(value[:10], "%Y-%m-%d").date()
    except ValueError:
        return None


async def _recent_form4s(
    client: httpx.AsyncClient, cik: str, cutoff: date
) -> tuple[list[tuple[str, str, date]], str | None]:
    """(accession, document, filed_on) for a company's recent Form 4s.

    Returns the filings and, when the lookup failed, a name for why. An empty
    list on its own cannot distinguish "this company filed nothing this week"
    from "the request was refused", and those need opposite responses.
    """
    try:
        response = await client.get(
            SUBMISSIONS_URL.format(cik=cik), headers=_headers(), timeout=30.0
        )
        response.raise_for_status()
        payload = response.json()
    except httpx.HTTPStatusError as exc:
        logger.warning("SEC submissions failed for CIK %s: %s", cik, exc)
        return [], f"HTTP {exc.response.status_code}"
    except (httpx.HTTPError, ValueError) as exc:
        logger.warning("SEC submissions failed for CIK %s: %s", cik, exc)
        return [], type(exc).__name__

    recent = payload.get("filings", {}).get("recent", {})
    forms = recent.get("form", [])
    accessions = recent.get("accessionNumber", [])
    documents = recent.get("primaryDocument", [])
    dates = recent.get("filingDate", [])

    found: list[tuple[str, str, date]] = []
    for index, form in enumerate(forms):
        if form != "4":
            continue
        filed = _parse_date(dates[index] if index < len(dates) else None)
        if filed is None or filed < cutoff:
            continue
        accession = accessions[index] if index < len(accessions) else ""
        document = documents[index] if index < len(documents) else ""
        if accession and document:
            found.append((accession.replace("-", ""), document, filed))
    return found, None


async def ingest_insider_transactions(
    db: AsyncSession,
    tickers: list[str] | None = None,
    lookback_days: int | None = None,
) -> InsiderReport:
    """Fetch and store recent open-market insider trades for tracked symbols.

    Scoped to the tracked universe rather than the whole firehose. Every US
    issuer files these, and the daily feed is thousands of rows of which a
    handful are about companies here — filtering by CIK first turns an
    impossible fetch into a few dozen requests.
    """
    report = InsiderReport()
    settings = get_settings()
    cutoff = (
        datetime.now(timezone.utc).date()
        - timedelta(days=lookback_days or DEFAULT_LOOKBACK_DAYS)
    )

    query = select(Stock).where(Stock.is_active.is_(True), Stock.cik.is_not(None))
    if tickers:
        query = query.where(Stock.ticker.in_([t.upper() for t in tickers]))
    stocks = list((await db.execute(query)).scalars())

    if not stocks:
        report.note = (
            "No tracked symbol has a CIK. Form 4 is a US filing, so this needs "
            "POST /admin/ingest/sec to resolve CIKs first."
        )
        return report

    report.symbols = len(stocks)
    async with httpx.AsyncClient() as client:
        for stock in stocks:
            filings, failure = await _recent_form4s(client, stock.cik, cutoff)
            await asyncio.sleep(REQUEST_DELAY_SECONDS)
            if failure is not None:
                report.lookup_failures[failure] = (
                    report.lookup_failures.get(failure, 0) + 1
                )
            report.filings_seen += len(filings)

            for accession, document, filed_on in filings:
                if await _already_stored(db, accession):
                    continue
                xml = await _fetch_document(client, stock.cik, accession, document)
                await asyncio.sleep(REQUEST_DELAY_SECONDS)
                if xml is None:
                    report.failed.append(f"{stock.ticker}:{accession}")
                    continue

                rows = parse_form4(xml)
                if rows:
                    report.filings_parsed += 1
                for row in rows:
                    if not row["open_market"]:
                        report.skipped_not_open_market += 1
                        continue
                    if row["traded_on"] is None:
                        continue
                    db.add(
                        InsiderTransaction(
                            ticker_id=stock.id,
                            accession=accession,
                            sequence=row["sequence"],
                            traded_on=row["traded_on"],
                            filed_on=filed_on,
                            insider_name=row["insider_name"],
                            insider_title=row["insider_title"],
                            transaction_code=row["code"],
                            shares=row["shares"],
                            price_per_share=row["price_per_share"],
                            value=row["value"],
                        )
                    )
                    report.transactions_stored += 1

    await db.commit()
    logger.info("Insider transactions: %s", report.as_dict())
    return report


async def _already_stored(db: AsyncSession, accession: str) -> bool:
    existing = await db.execute(
        select(InsiderTransaction.id).where(InsiderTransaction.accession == accession).limit(1)
    )
    return existing.scalar_one_or_none() is not None


async def _fetch_document(
    client: httpx.AsyncClient, cik: str, accession: str, document: str
) -> str | None:
    url = ARCHIVE_URL.format(
        cik=int(cik), accession=accession, document=raw_xml_document(document)
    )
    try:
        response = await client.get(url, headers=_headers(), timeout=30.0)
        response.raise_for_status()
    except httpx.HTTPError as exc:
        logger.debug("Form 4 fetch failed for %s: %s", url, exc)
        return None
    return response.text
