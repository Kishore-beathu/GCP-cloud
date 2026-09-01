"""Finnhub company-news ingestion.

One ``/company-news`` call covers one ticker over a date range, so the
scheduler rotates through the universe in batches sized to the account's
rate limit (free tier: ~60 calls/min).

Requires ``FINNHUB_API_KEY``; without it every entry point logs once and
returns an empty report so the rest of the platform keeps running.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import date, datetime, timedelta, timezone

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models import Stock
from app.services.ingest import IngestReport, RawArticle, store_articles
from app.services.prices import Quote, upsert_quotes

logger = logging.getLogger(__name__)

BASE_URL = "https://finnhub.io/api/v1"
SOURCE = "finnhub"
REQUEST_DELAY_SECONDS = 1.1  # ~55 calls/min, inside the free tier's 60


class FinnhubRateLimited(Exception):
    """Raised when Finnhub answers 429; the current batch should stop."""


class FinnhubNotCovered(Exception):
    """One symbol or endpoint is outside the plan, rather than the key being bad.

    Finnhub answers 403 for both "your key is not valid here" and "this symbol
    is not in your plan", and the second is per-symbol. Treating them alike
    stopped the whole quote batch on the first non-US ticker — and because the
    batch is ordered by ticker, that was 000660.KS, the first symbol of all.
    Every US quote after it was abandoned before being asked for.
    """


class FinnhubRejected(Exception):
    """Raised on 401/403: a bad key, or an endpoint the plan does not include.

    Distinct from rate limiting because the remedy is different and the batch
    must not continue: repeating a rejected call across 87 symbols produces 87
    identical warnings and an empty result that looks like "no news".
    """


def _parse_news_item(ticker: str, item: dict) -> RawArticle | None:
    """Convert one Finnhub news payload entry, or None if it is unusable."""
    headline = str(item.get("headline") or "").strip()
    url = str(item.get("url") or "").strip()
    published_unix = item.get("datetime")
    if not headline or not url or not published_unix:
        return None

    try:
        published_at = datetime.fromtimestamp(int(published_unix), tz=timezone.utc)
    except (ValueError, OSError, OverflowError):
        logger.debug("Finnhub item for %s has invalid timestamp %r", ticker, published_unix)
        return None

    summary = str(item.get("summary") or "").strip() or None
    return RawArticle(
        ticker=ticker,
        headline=headline,
        body=summary,
        url=url,
        source=SOURCE,
        published_at=published_at,
    )


async def fetch_company_news(
    client: httpx.AsyncClient,
    ticker: str,
    api_key: str,
    from_date: date,
    to_date: date,
) -> list[RawArticle]:
    """Fetch one ticker's news window. Raises FinnhubRateLimited on 429."""
    try:
        response = await client.get(
            f"{BASE_URL}/company-news",
            params={
                "symbol": ticker,
                "from": from_date.isoformat(),
                "to": to_date.isoformat(),
                "token": api_key,
            },
            timeout=30.0,
        )
        if response.status_code == 429:
            raise FinnhubRateLimited(ticker)
        if response.status_code == 403:
            raise FinnhubNotCovered(f"{ticker}: {response.text[:120]}")
        if response.status_code == 401:
            raise FinnhubRejected(
                f"HTTP {response.status_code} for {ticker}: {response.text[:200]}"
            )
        response.raise_for_status()
        payload = response.json()
    except (FinnhubRateLimited, FinnhubRejected, FinnhubNotCovered):
        raise
    except (httpx.HTTPError, ValueError) as exc:
        logger.warning("Finnhub request failed for %s: %s", ticker, exc)
        return []

    if not isinstance(payload, list):
        logger.warning("Finnhub returned unexpected payload for %s: %r", ticker, type(payload))
        return []

    articles = []
    for item in payload:
        article = _parse_news_item(ticker, item)
        if article is not None:
            articles.append(article)
    return articles


async def ingest_finnhub_news(
    db: AsyncSession,
    tickers: list[str] | None = None,
    lookback_days: int | None = None,
) -> IngestReport:
    """Fetch and store news for the given tickers (default: all active)."""
    settings = get_settings()
    if not settings.finnhub_api_key:
        logger.info("Finnhub ingest skipped: FINNHUB_API_KEY is not set")
        return IngestReport()

    query = select(Stock.ticker).where(Stock.is_active.is_(True)).order_by(Stock.ticker)
    if tickers:
        query = query.where(Stock.ticker.in_([t.upper() for t in tickers]))
    symbols = list((await db.execute(query)).scalars())
    if not symbols:
        logger.info("Finnhub ingest: no matching stocks")
        return IngestReport()

    to_date = datetime.now(timezone.utc).date()
    from_date = to_date - timedelta(days=lookback_days or settings.finnhub_lookback_days)

    collected: list[RawArticle] = []
    async with httpx.AsyncClient() as client:
        for index, symbol in enumerate(symbols):
            try:
                collected.extend(
                    await fetch_company_news(
                        client, symbol, settings.finnhub_api_key, from_date, to_date
                    )
                )
            except FinnhubRateLimited:
                logger.warning(
                    "Finnhub rate limit hit at %s (%d/%d); storing what we have",
                    symbol,
                    index,
                    len(symbols),
                )
                break
            except FinnhubRejected as exc:
                logger.error(
                    "Finnhub rejected the request, stopping the batch: %s. "
                    "Check FINNHUB_API_KEY and what your plan covers.",
                    exc,
                )
                break
            if index < len(symbols) - 1:
                await asyncio.sleep(REQUEST_DELAY_SECONDS)

    return await store_articles(db, collected)


# --- Quotes ------------------------------------------------------------------
# Finnhub's /quote is the practical price source for this platform. Alpha
# Vantage's free tier allows ~25 calls a day, which cannot populate an
# 87-symbol watchlist even once; Finnhub's allows ~60 a minute, which covers
# the whole universe in under two minutes and can be repeated all day.


def parse_quote(ticker: str, payload: dict) -> Quote | None:
    """Convert a /quote response into a Quote, or None if the symbol is uncovered.

    Finnhub answers HTTP 200 for a symbol it does not carry, with every field
    zeroed and ``t`` (the quote timestamp) set to 0. Treating that as a real
    price would write a 0.00 close over the whole non-US half of the universe.
    """
    close = payload.get("c")
    timestamp = payload.get("t")
    if not close or not timestamp:
        return None

    try:
        trading_day = datetime.fromtimestamp(int(timestamp), tz=timezone.utc).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
    except (ValueError, OSError, OverflowError):
        logger.debug("Finnhub quote for %s has invalid timestamp %r", ticker, timestamp)
        return None

    def number(key: str) -> float | None:
        value = payload.get(key)
        return float(value) if isinstance(value, (int, float)) and value else None

    return Quote(
        symbol=ticker,
        open=number("o"),
        high=number("h"),
        low=number("l"),
        close=float(close),
        volume=None,  # /quote carries no volume; the daily backfill does.
        trading_day=trading_day,
    )


async def fetch_quote(
    client: httpx.AsyncClient, ticker: str, api_key: str
) -> Quote | None:
    """Fetch one symbol's latest quote. Raises on rate limiting or rejection."""
    try:
        response = await client.get(
            f"{BASE_URL}/quote",
            params={"symbol": ticker, "token": api_key},
            timeout=30.0,
        )
        if response.status_code == 429:
            raise FinnhubRateLimited(ticker)
        if response.status_code == 403:
            raise FinnhubNotCovered(f"{ticker}: {response.text[:120]}")
        if response.status_code == 401:
            raise FinnhubRejected(
                f"HTTP {response.status_code} for {ticker}: {response.text[:200]}"
            )
        response.raise_for_status()
        payload = response.json()
    except (FinnhubRateLimited, FinnhubRejected, FinnhubNotCovered):
        raise
    except (httpx.HTTPError, ValueError) as exc:
        logger.warning("Finnhub quote failed for %s: %s", ticker, exc)
        return None

    if not isinstance(payload, dict):
        return None
    return parse_quote(ticker, payload)


async def update_finnhub_quotes(
    db: AsyncSession, tickers: list[str] | None = None
) -> dict[str, int]:
    """Refresh the latest price for the given tickers (default: all active)."""
    settings = get_settings()
    # "uncovered" is a symbol the vendor knows but has no quote for today;
    # "not_in_plan" is one it refuses to answer for at all. Separate counters
    # because the first is normal and the second says the universe has outgrown
    # the subscription.
    totals = {"inserted": 0, "updated": 0, "uncovered": 0, "not_in_plan": 0}
    if not settings.finnhub_api_key:
        logger.info("Finnhub quote refresh skipped: FINNHUB_API_KEY is not set")
        return totals

    query = select(Stock).where(Stock.is_active.is_(True)).order_by(Stock.ticker)
    if tickers:
        query = query.where(Stock.ticker.in_([t.upper() for t in tickers]))
    stocks = list((await db.execute(query)).scalars())

    async with httpx.AsyncClient() as client:
        for index, stock in enumerate(stocks):
            try:
                quote = await fetch_quote(client, stock.ticker, settings.finnhub_api_key)
            except FinnhubRateLimited:
                logger.warning("Finnhub rate limit hit at %s; keeping what we have", stock.ticker)
                break
            except FinnhubNotCovered:
                # One symbol outside the plan is not a reason to abandon the
                # rest. Ordered by ticker, the non-US names sort first, so
                # aborting here skipped every US quote in the universe.
                totals["not_in_plan"] += 1
                if index < len(stocks) - 1:
                    await asyncio.sleep(REQUEST_DELAY_SECONDS)
                continue
            except FinnhubRejected as exc:
                logger.error("Finnhub rejected the quote request, stopping: %s", exc)
                break

            if quote is None:
                totals["uncovered"] += 1
            else:
                result = await upsert_quotes(db, stock, [quote], source=SOURCE)
                totals["inserted"] += result["inserted"]
                totals["updated"] += result["updated"]

            if index < len(stocks) - 1:
                await asyncio.sleep(REQUEST_DELAY_SECONDS)

    if totals["not_in_plan"] and not (totals["inserted"] or totals["updated"]):
        logger.warning(
            "Finnhub returned no quotes: all %d attempted symbols were refused. "
            "That reads as a plan or key problem rather than symbol coverage.",
            totals["not_in_plan"],
        )
    elif totals["not_in_plan"]:
        logger.info(
            "Finnhub: %d symbols are outside the plan and were skipped",
            totals["not_in_plan"],
        )

    logger.info("Finnhub quote refresh complete: %s", totals)
    return totals


# --- Fundamentals, earnings and analyst opinion -------------------------------
# All three are on the free tier for US symbols and absent for most others,
# which is stated rather than discovered: a European listing simply returns
# nothing here, and the caller reports it as uncovered rather than as an error.


async def _get(
    client: httpx.AsyncClient, path: str, params: dict, label: str
) -> object | None:
    """One Finnhub GET, with the refusal rules the rest of this module uses.

    A 401/403 is about the account and applies to every subsequent call, so it
    raises and stops the batch. Anything else degrades to None: a symbol the
    free tier does not cover is an ordinary outcome, not a failure.
    """
    try:
        response = await client.get(f"{BASE_URL}{path}", params=params, timeout=30.0)
        if response.status_code == 429:
            raise FinnhubRateLimited(label)
        if response.status_code == 403:
            raise FinnhubNotCovered(f"{label}: {response.text[:120]}")
        if response.status_code == 401:
            raise FinnhubRejected(
                f"HTTP {response.status_code} for {label}: {response.text[:200]}"
            )
        response.raise_for_status()
        return response.json()
    except (FinnhubRateLimited, FinnhubRejected, FinnhubNotCovered):
        raise
    except (httpx.HTTPError, ValueError) as exc:
        logger.warning("Finnhub request failed for %s: %s", label, exc)
        return None


# The metric keys worth storing, mapped to our column names.
#
# A deliberately short list out of the hundred-odd Finnhub returns. Each one
# answers a question the score cannot currently ask — is this expensive, is it
# growing, does it convert revenue into profit — and every extra one is a
# factor that would have to earn its weight through validate() like the others.
# Adding ninety more because they are in the payload is how a score stops being
# explainable.
_METRIC_KEYS = {
    "peNormalizedAnnual": "pe_ratio",
    "psAnnual": "ps_ratio",
    "pbAnnual": "pb_ratio",
    "evEbitdaAnnual": "ev_ebitda",
    "grossMarginAnnual": "gross_margin",
    "revenueGrowthTTMYoy": "revenue_growth_yoy",
    "roeTTM": "return_on_equity",
}


async def fetch_metrics(
    client: httpx.AsyncClient, ticker: str, api_key: str
) -> dict | None:
    """Valuation and quality ratios for one symbol.

    The largest capability gap against a commercial score: this platform could
    say a stock had good news and rising price, and nothing about whether it
    was expensive.

    Ratios are stored as reported, which means they carry the vendor's
    conventions — a negative P/E for a loss-making company, nulls where a ratio
    is undefined. Neither is cleaned up here: a made-up number would rank, and
    ranking is exactly what this data is for.
    """
    payload = await _get(
        client,
        "/stock/metric",
        {"symbol": ticker, "metric": "all", "token": api_key},
        ticker,
    )
    if not isinstance(payload, dict):
        return None
    metrics = payload.get("metric")
    if not isinstance(metrics, dict) or not metrics:
        return None

    values: dict[str, float | None] = {}
    for vendor_key, column in _METRIC_KEYS.items():
        raw = metrics.get(vendor_key)
        try:
            values[column] = float(raw) if raw is not None else None
        except (TypeError, ValueError):
            values[column] = None

    # Every field null means the vendor answered without covering this symbol,
    # which is a different fact from "covered, and these are the numbers".
    if not any(value is not None for value in values.values()):
        return None
    return values


async def fetch_profile(
    client: httpx.AsyncClient, ticker: str, api_key: str
) -> dict | None:
    """Company profile: market cap and shares outstanding.

    Finnhub reports market cap in millions of the listing currency. It is
    converted here so a stored value is in units, because a column holding
    millions for one row and units for another is the kind of thing that is
    only discovered by a filter quietly returning the wrong companies.
    """
    payload = await _get(
        client, "/stock/profile2", {"symbol": ticker, "token": api_key}, ticker
    )
    if not isinstance(payload, dict) or not payload:
        return None

    cap_millions = payload.get("marketCapitalization")
    shares_millions = payload.get("shareOutstanding")
    return {
        "market_cap": float(cap_millions) * 1_000_000 if cap_millions else None,
        "shares_outstanding": (
            float(shares_millions) * 1_000_000 if shares_millions else None
        ),
        "currency": payload.get("currency"),
        "name": payload.get("name"),
    }


def _surprise_pct(actual: float | None, estimate: float | None) -> float | None:
    """Surprise as a percentage of the estimate, where that means anything.

    The ordinary formula breaks on the cases that matter most. A zero estimate
    divides by zero. A *negative* estimate inverts the sign, so a loss-making
    company that lost less than feared reads as a miss — which is exactly
    backwards, and common in biotech where most of this universe's small caps
    live. Both return None rather than a number that would be quietly wrong.
    """
    if actual is None or estimate is None or estimate <= 0:
        return None
    return round((actual - estimate) / abs(estimate) * 100, 4)


async def fetch_earnings(
    client: httpx.AsyncClient, ticker: str, api_key: str
) -> list[dict]:
    """The last few reported quarters: actual against estimate."""
    payload = await _get(
        client, "/stock/earnings", {"symbol": ticker, "token": api_key}, ticker
    )
    if not isinstance(payload, list):
        return []

    reports: list[dict] = []
    for item in payload:
        if not isinstance(item, dict):
            continue
        period = _parse_date(item.get("period"))
        if period is None:
            continue
        actual, estimate = item.get("actual"), item.get("estimate")
        reports.append(
            {
                "period": period,
                "eps_actual": float(actual) if actual is not None else None,
                "eps_estimate": float(estimate) if estimate is not None else None,
                "eps_surprise_pct": _surprise_pct(actual, estimate),
            }
        )
    return reports


async def fetch_recommendations(
    client: httpx.AsyncClient, ticker: str, api_key: str
) -> list[dict]:
    """Monthly analyst recommendation counts.

    A free stand-in for estimate revisions. The counts on their own say little
    — sell-side opinion is structurally bullish — but the month-on-month
    change says which way it is moving, and that is what the scoring reads.
    """
    payload = await _get(
        client, "/stock/recommendation", {"symbol": ticker, "token": api_key}, ticker
    )
    if not isinstance(payload, list):
        return []

    trends: list[dict] = []
    for item in payload:
        if not isinstance(item, dict):
            continue
        period = _parse_date(item.get("period"))
        if period is None:
            continue
        trends.append(
            {
                "period": period,
                "strong_buy": int(item.get("strongBuy") or 0),
                "buy": int(item.get("buy") or 0),
                "hold": int(item.get("hold") or 0),
                "sell": int(item.get("sell") or 0),
                "strong_sell": int(item.get("strongSell") or 0),
            }
        )
    return trends


async def fetch_earnings_calendar(
    client: httpx.AsyncClient, api_key: str, from_date: date, to_date: date
) -> list[dict]:
    """Scheduled earnings dates across the whole market, in one call.

    One request for a date range rather than one per symbol: the calendar
    endpoint returns every company reporting in the window, and filtering to
    the tracked universe afterwards costs nothing.
    """
    payload = await _get(
        client,
        "/calendar/earnings",
        {"from": from_date.isoformat(), "to": to_date.isoformat(), "token": api_key},
        "earnings calendar",
    )
    if not isinstance(payload, dict):
        return []

    events: list[dict] = []
    for item in payload.get("earningsCalendar") or []:
        if not isinstance(item, dict):
            continue
        when = _parse_date(item.get("date"))
        symbol = (item.get("symbol") or "").strip().upper()
        if when is None or not symbol:
            continue
        events.append(
            {
                "symbol": symbol,
                "expected_at": when,
                # "bmo" before market open, "amc" after close, "dmh" during.
                "hour": (item.get("hour") or "").strip(),
                "eps_estimate": item.get("epsEstimate"),
                "revenue_estimate": item.get("revenueEstimate"),
                "quarter": item.get("quarter"),
                "year": item.get("year"),
            }
        )
    return events


def _parse_date(value: object) -> datetime | None:
    """Finnhub dates arrive as YYYY-MM-DD strings; anything else is skipped."""
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        return datetime.fromisoformat(value.strip()).replace(tzinfo=timezone.utc)
    except ValueError:
        return None
