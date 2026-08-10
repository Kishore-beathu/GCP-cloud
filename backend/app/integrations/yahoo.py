"""Global price history and quotes, for the listings no keyed vendor covers.

Finnhub's free tier quotes US symbols; Alpha Vantage's allows ~25 calls a day.
Neither can price the European and Asia-Pacific half of this universe, which
left those rows permanently blank. Yahoo's chart endpoint covers essentially
every listing here, needs no API key, and returns the current price *and* the
daily history in a single call.

Two things to be clear about before relying on it:

* **It is undocumented.** There is no published contract, no SLA and no
  deprecation notice; it can change shape or start refusing traffic without
  warning. Everything here therefore fails soft — a symbol that stops parsing
  is skipped and counted, never raised into the ingest loop.
* **Check Yahoo's terms for your use.** Personal analysis is one thing;
  redistributing the data is another. This module is rate-limited to be a
  polite client, and the source tag on every row it writes is ``yahoo``, so
  you can identify and delete it if you switch to a licensed feed.

Set ``YAHOO_PRICES_ENABLED=false`` to turn it off entirely.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

import httpx
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models import Stock, StockPrice
from app.services import markets
from app.services.prices import Quote, upsert_quotes

logger = logging.getLogger(__name__)

CHART_URL = "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
SOURCE = "yahoo"
REQUEST_DELAY_SECONDS = 0.4

# Yahoo refuses a bare client. Identify the application honestly rather than
# impersonating a browser.
HEADERS = {"User-Agent": "trading-intelligence-agent/1.0 (+personal research use)"}


class YahooUnavailable(Exception):
    """Raised when Yahoo refuses traffic outright; the batch should stop."""


def parse_chart(ticker: str, payload: dict) -> list[Quote]:
    """Turn a chart response into daily quotes, oldest first.

    Prices arrive in the venue's quoting unit, which for London is pence. They
    are converted to the major unit here so a stored close means the same thing
    for every venue — otherwise `AZN.L` reads a hundred times `AZN`.
    """
    chart = payload.get("chart") or {}
    results = chart.get("result")
    if not results:
        return []

    result = results[0]
    timestamps = result.get("timestamp") or []
    quote_blocks = (result.get("indicators") or {}).get("quote") or [{}]
    bars = quote_blocks[0] if quote_blocks else {}

    closes = bars.get("close") or []
    opens = bars.get("open") or []
    highs = bars.get("high") or []
    lows = bars.get("low") or []
    volumes = bars.get("volume") or []

    market = markets.resolve(ticker)

    def scaled(series: list, index: int) -> float | None:
        if index >= len(series):
            return None
        value = series[index]
        if value is None:
            return None
        return markets.normalise_price(float(value), market) if market else float(value)

    quotes: list[Quote] = []
    for index, stamp in enumerate(timestamps):
        close = scaled(closes, index)
        if close is None:
            # Yahoo pads the series with nulls for non-trading sessions.
            continue
        try:
            trading_day = datetime.fromtimestamp(int(stamp), tz=timezone.utc).replace(
                hour=0, minute=0, second=0, microsecond=0
            )
        except (ValueError, OSError, OverflowError):
            continue

        volume = volumes[index] if index < len(volumes) else None
        quotes.append(
            Quote(
                symbol=ticker,
                open=scaled(opens, index),
                high=scaled(highs, index),
                low=scaled(lows, index),
                close=close,
                volume=int(volume) if volume else None,
                trading_day=trading_day,
            )
        )

    quotes.sort(key=lambda quote: quote.trading_day)
    return quotes


async def fetch_chart(
    client: httpx.AsyncClient, ticker: str, range_: str = "3mo"
) -> list[Quote]:
    """Fetch one symbol's daily series. Returns [] for a symbol Yahoo lacks."""
    try:
        response = await client.get(
            CHART_URL.format(symbol=ticker),
            params={"interval": "1d", "range": range_, "includePrePost": "false"},
            headers=HEADERS,
            timeout=30.0,
        )
    except httpx.HTTPError as exc:
        logger.warning("Yahoo request failed for %s: %s", ticker, exc)
        return []

    if response.status_code == 429:
        raise YahooUnavailable(f"HTTP 429 at {ticker} - being rate limited")
    if response.status_code in (401, 403):
        raise YahooUnavailable(f"HTTP {response.status_code} at {ticker} - refused")
    if response.status_code == 404:
        # A symbol Yahoo does not carry. Normal for an obscure listing.
        logger.debug("Yahoo has no symbol %s", ticker)
        return []
    if response.status_code != 200:
        logger.warning("Yahoo returned HTTP %s for %s", response.status_code, ticker)
        return []

    try:
        payload = response.json()
    except ValueError:
        logger.warning("Yahoo returned non-JSON for %s", ticker)
        return []

    error = (payload.get("chart") or {}).get("error")
    if error:
        logger.debug("Yahoo error for %s: %s", ticker, error)
        return []

    return parse_chart(ticker, payload)


async def _stocks_to_price(
    db: AsyncSession, tickers: list[str] | None, only_missing: bool
) -> list[Stock]:
    query = select(Stock).where(Stock.is_active.is_(True)).order_by(Stock.ticker)
    if tickers:
        query = query.where(Stock.ticker.in_([t.upper() for t in tickers]))
    stocks = list((await db.execute(query)).scalars())

    if not only_missing:
        return stocks

    # "Everything still showing a dash" — the symbols with no stored price at all.
    priced = {
        row[0]
        for row in (
            await db.execute(select(StockPrice.ticker_id).group_by(StockPrice.ticker_id))
        ).all()
    }
    return [stock for stock in stocks if stock.id not in priced]


async def update_yahoo_prices(
    db: AsyncSession,
    tickers: list[str] | None = None,
    range_: str | None = None,
    only_missing: bool = False,
) -> dict[str, int]:
    """Load current price and daily history for the given symbols.

    One call per symbol returns both, so this fills the chart, the watchlist
    price and the backtester in a single pass.
    """
    settings = get_settings()
    totals = {"symbols": 0, "inserted": 0, "updated": 0, "uncovered": 0}
    if not settings.yahoo_prices_enabled:
        logger.info("Yahoo price load skipped: YAHOO_PRICES_ENABLED is false")
        return totals

    stocks = await _stocks_to_price(db, tickers, only_missing)
    totals["symbols"] = len(stocks)
    if not stocks:
        return totals

    window = range_ or settings.yahoo_price_range
    async with httpx.AsyncClient() as client:
        for index, stock in enumerate(stocks):
            try:
                quotes = await fetch_chart(client, stock.ticker, window)
            except YahooUnavailable as exc:
                logger.warning("Yahoo stopped the batch at %s: %s", stock.ticker, exc)
                break

            if not quotes:
                totals["uncovered"] += 1
            else:
                result = await upsert_quotes(db, stock, quotes, source=SOURCE)
                totals["inserted"] += result["inserted"]
                totals["updated"] += result["updated"]

            if index < len(stocks) - 1:
                await asyncio.sleep(REQUEST_DELAY_SECONDS)

    logger.info("Yahoo price load complete: %s", totals)
    return totals


async def count_unpriced(db: AsyncSession) -> int:
    """How many active symbols still have no stored price."""
    total = (
        await db.execute(
            select(func.count(Stock.id)).where(Stock.is_active.is_(True))
        )
    ).scalar_one()
    priced = (
        await db.execute(select(func.count(func.distinct(StockPrice.ticker_id))))
    ).scalar_one()
    return max(0, total - priced)
