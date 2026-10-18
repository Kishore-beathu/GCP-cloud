"""Alpha Vantage price ingestion.

Two modes share the ``stock_prices`` table:

* ``update_quotes`` — GLOBAL_QUOTE per ticker, giving the latest trading day's
  OHLCV. The scheduler runs this in small rotating batches sized to the
  account's rate limit (free tier: 5 calls/min). Repeated calls on the same
  trading day update the existing row in place, so intraday refreshes don't
  multiply rows.
* ``backfill_daily`` — TIME_SERIES_DAILY per ticker, loading up to 100 days
  (or 20+ years with ``outputsize=full``) of history in one call. This is what
  makes backtesting useful on day one.

Requires ``ALPHA_VANTAGE_API_KEY``; without it every entry point logs once and
returns an empty result so the rest of the platform keeps running.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models import Stock, StockPrice
from app.services.prices import Quote, upsert_quotes as _upsert_quotes
from app.services.redaction import redact, secrets_from

logger = logging.getLogger(__name__)

BASE_URL = "https://www.alphavantage.co/query"
SOURCE = "alpha_vantage"
REQUEST_DELAY_SECONDS = 12.5  # ~4.8 calls/min, inside the free tier's 5


class AlphaVantageThrottled(Exception):
    """Raised when the API returns its rate-limit note; the batch should stop."""


class AlphaVantageRejected(Exception):
    """Raised when the API rejects the call outright.

    A bad key, an unsupported symbol and a premium-only endpoint all arrive as
    HTTP 200 with an explanatory string, so without this they are
    indistinguishable from a symbol that genuinely has no data.
    """


def _to_float(value: object) -> float | None:
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def _to_int(value: object) -> int | None:
    try:
        return int(float(value))  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def _check_throttle(payload: dict) -> None:
    """Surface Alpha Vantage's own explanation instead of returning empty data.

    Every failure mode here is an HTTP 200 whose body carries the reason:
    rate limiting under ``Note``/``Information``, and rejected calls under
    ``Error Message``. Both used to fall through to "no rows parsed", which
    reads to a caller exactly like a symbol with no history.
    """
    for key in ("Note", "Information"):
        message = payload.get(key)
        if not message:
            continue
        text = str(message)
        lowered = text.lower()
        if "call frequency" in text or "rate limit" in lowered:
            raise AlphaVantageThrottled(text)
        # "Information" also carries premium-endpoint and bad-key notices.
        raise AlphaVantageRejected(text)

    error = payload.get("Error Message")
    if error:
        raise AlphaVantageRejected(str(error))


def parse_global_quote(symbol: str, payload: dict) -> Quote | None:
    """Extract a Quote from a GLOBAL_QUOTE response, or None if unusable."""
    _check_throttle(payload)
    data = payload.get("Global Quote") or {}
    close = _to_float(data.get("05. price"))
    trading_day_raw = data.get("07. latest trading day")
    if close is None or not trading_day_raw:
        return None

    try:
        trading_day = datetime.strptime(str(trading_day_raw), "%Y-%m-%d").replace(
            tzinfo=timezone.utc
        )
    except ValueError:
        logger.debug("Alpha Vantage gave unparsable trading day %r for %s", trading_day_raw, symbol)
        return None

    return Quote(
        symbol=symbol,
        open=_to_float(data.get("02. open")),
        high=_to_float(data.get("03. high")),
        low=_to_float(data.get("04. low")),
        close=close,
        volume=_to_int(data.get("06. volume")),
        trading_day=trading_day,
    )


def parse_daily_series(symbol: str, payload: dict) -> list[Quote]:
    """Extract the full series from a TIME_SERIES_DAILY response, oldest first."""
    _check_throttle(payload)
    series = payload.get("Time Series (Daily)") or {}
    quotes: list[Quote] = []
    for day_raw, bar in series.items():
        close = _to_float(bar.get("4. close"))
        if close is None:
            continue
        try:
            trading_day = datetime.strptime(day_raw, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        except ValueError:
            continue
        quotes.append(
            Quote(
                symbol=symbol,
                open=_to_float(bar.get("1. open")),
                high=_to_float(bar.get("2. high")),
                low=_to_float(bar.get("3. low")),
                close=close,
                volume=_to_int(bar.get("5. volume")),
                trading_day=trading_day,
            )
        )
    quotes.sort(key=lambda quote: quote.trading_day)
    return quotes


async def fetch_global_quote(
    client: httpx.AsyncClient, symbol: str, api_key: str
) -> Quote | None:
    """Fetch the latest quote for one symbol. Raises AlphaVantageThrottled."""
    try:
        response = await client.get(
            BASE_URL,
            params={"function": "GLOBAL_QUOTE", "symbol": symbol, "apikey": api_key},
            timeout=30.0,
        )
        response.raise_for_status()
        return parse_global_quote(symbol, response.json())
    except (AlphaVantageThrottled, AlphaVantageRejected):
        raise
    except (httpx.HTTPError, ValueError) as exc:
        logger.warning("Alpha Vantage quote failed for %s: %s", symbol, exc)
        return None


async def fetch_daily_series(
    client: httpx.AsyncClient, symbol: str, api_key: str, outputsize: str = "compact"
) -> list[Quote]:
    """Fetch daily history for one symbol. Raises AlphaVantageThrottled."""
    try:
        response = await client.get(
            BASE_URL,
            params={
                "function": "TIME_SERIES_DAILY",
                "symbol": symbol,
                "outputsize": outputsize,
                "apikey": api_key,
            },
            timeout=60.0,
        )
        response.raise_for_status()
        return parse_daily_series(symbol, response.json())
    except (AlphaVantageThrottled, AlphaVantageRejected):
        raise
    except (httpx.HTTPError, ValueError) as exc:
        logger.warning("Alpha Vantage daily series failed for %s: %s", symbol, exc)
        return []


async def upsert_quotes(db: AsyncSession, stock: Stock, quotes: list[Quote]) -> dict[str, int]:
    """Store quotes attributed to Alpha Vantage."""
    return await _upsert_quotes(db, stock, quotes, source=SOURCE)


async def _load_stocks(db: AsyncSession, tickers: list[str] | None) -> list[Stock]:
    query = select(Stock).where(Stock.is_active.is_(True)).order_by(Stock.ticker)
    if tickers:
        query = query.where(Stock.ticker.in_([t.upper() for t in tickers]))
    return list((await db.execute(query)).scalars())


async def update_quotes(db: AsyncSession, tickers: list[str] | None = None) -> dict[str, int]:
    """Refresh the latest quote for the given tickers (default: all active)."""
    settings = get_settings()
    totals = {"inserted": 0, "updated": 0, "failed": 0}
    if not settings.alpha_vantage_api_key:
        logger.info("Quote update skipped: ALPHA_VANTAGE_API_KEY is not set")
        return totals

    stocks = await _load_stocks(db, tickers)
    async with httpx.AsyncClient() as client:
        for index, stock in enumerate(stocks):
            try:
                quote = await fetch_global_quote(
                    client, stock.ticker, settings.alpha_vantage_api_key
                )
            except AlphaVantageThrottled as exc:
                # Alpha Vantage quotes the key back inside its throttle notice,
                # so this message must be scrubbed at the source as well as by
                # the log formatter — the exception text also travels into API
                # responses, which the formatter never sees.
                logger.warning(
                    "Alpha Vantage throttled at %s: %s",
                    stock.ticker,
                    redact(str(exc), secrets_from(settings)),
                )
                break
            except AlphaVantageRejected as exc:
                # A rejection is about the account or the symbol, not timing.
                # Stop rather than spend the remaining quota repeating it.
                logger.error(
                    "Alpha Vantage rejected the call at %s: %s",
                    stock.ticker,
                    redact(str(exc), secrets_from(settings)),
                )
                break

            if quote is None:
                totals["failed"] += 1
            else:
                result = await upsert_quotes(db, stock, [quote])
                totals["inserted"] += result["inserted"]
                totals["updated"] += result["updated"]

            if index < len(stocks) - 1:
                await asyncio.sleep(REQUEST_DELAY_SECONDS)

    logger.info("Quote update complete: %s", totals)
    return totals


async def backfill_daily(
    db: AsyncSession, ticker: str, outputsize: str = "compact"
) -> dict[str, int]:
    """Load daily price history for one ticker into stock_prices."""
    stocks = await _load_stocks(db, [ticker])
    if not stocks:
        raise LookupError(f"Unknown ticker {ticker.upper()}")

    settings = get_settings()
    if not settings.alpha_vantage_api_key:
        logger.info("Backfill skipped: ALPHA_VANTAGE_API_KEY is not set")
        return {
            "inserted": 0,
            "updated": 0,
            "note": "ALPHA_VANTAGE_API_KEY is not set, so no request was made.",
        }

    symbol = stocks[0].ticker
    try:
        async with httpx.AsyncClient() as client:
            quotes = await fetch_daily_series(
                client, symbol, settings.alpha_vantage_api_key, outputsize
            )
    except AlphaVantageThrottled as exc:
        # Alpha Vantage quotes the API key back inside its rate-limit notice,
        # and this note is returned over the API, so it must be scrubbed.
        note = redact(f"Rate limited: {exc}", secrets_from(settings))
        logger.warning("Backfill for %s hit the rate limit: %s", symbol, note)
        return {"inserted": 0, "updated": 0, "note": note}
    except AlphaVantageRejected as exc:
        note = redact(f"Rejected by Alpha Vantage: {exc}", secrets_from(settings))
        logger.warning("Backfill for %s rejected: %s", symbol, note)
        return {"inserted": 0, "updated": 0, "note": note}

    result = await upsert_quotes(db, stocks[0], quotes)
    logger.info("Backfill for %s: %s", symbol, result)
    if not quotes:
        # A 200 with an empty series and no message: the symbol parsed but the
        # vendor has no history for it. Common for non-US listings.
        result["note"] = (
            f"Alpha Vantage returned no history for {symbol}. Its coverage of "
            "non-US listings is partial; try the US line if the company has one."
        )
    return result
