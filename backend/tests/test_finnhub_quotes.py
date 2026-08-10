"""Finnhub quotes: the price source that a free plan can actually fill.

Alpha Vantage's free tier allows ~25 calls a day, which cannot populate an
87-symbol watchlist even once. Finnhub's allows ~60 a minute.
"""

from __future__ import annotations

from datetime import timezone

import httpx
import pytest
from sqlalchemy import func, select

from app.config import get_settings
from app.integrations.finnhub import (
    FinnhubRejected,
    fetch_quote,
    parse_quote,
    update_finnhub_quotes,
)
from app.models import StockPrice

# A real /quote response: current, change, percent, high, low, open, previous
# close, and the quote timestamp.
QUOTE_PAYLOAD = {
    "c": 261.74,
    "d": -0.36,
    "dp": -0.1374,
    "h": 263.31,
    "l": 260.68,
    "o": 261.07,
    "pc": 262.1,
    "t": 1786665600,
}

# What Finnhub returns for a symbol it does not carry: zeros throughout.
UNCOVERED_PAYLOAD = {"c": 0, "d": None, "dp": None, "h": 0, "l": 0, "o": 0, "pc": 0, "t": 0}


def test_parse_quote_reads_the_ohlc():
    quote = parse_quote("PFE", QUOTE_PAYLOAD)

    assert quote is not None
    assert quote.close == 261.74
    assert quote.open == 261.07
    assert quote.high == 263.31
    assert quote.trading_day.tzinfo is timezone.utc
    # Normalised to midnight so it collides with the same day from any source.
    assert quote.trading_day.hour == 0
    assert quote.volume is None


def test_parse_quote_rejects_an_uncovered_symbol():
    """Zeros must not be stored as a real 0.00 close across the non-US universe."""
    assert parse_quote("068270.KS", UNCOVERED_PAYLOAD) is None


def test_parse_quote_rejects_a_missing_timestamp():
    assert parse_quote("PFE", {"c": 100.0, "t": 0}) is None


def _client(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


@pytest.mark.asyncio
async def test_fetch_quote_raises_on_a_rejected_key():
    async with _client(lambda request: httpx.Response(401, text="bad token")) as client:
        with pytest.raises(FinnhubRejected):
            await fetch_quote(client, "PFE", "key")


@pytest.mark.asyncio
async def test_update_stores_prices_and_counts_uncovered(db, seeded_stocks, monkeypatch):
    """MRNA is covered, PFE is not; both outcomes are counted, neither crashes."""
    monkeypatch.setenv("FINNHUB_API_KEY", "test-key")
    monkeypatch.setattr("app.integrations.finnhub.REQUEST_DELAY_SECONDS", 0)
    get_settings.cache_clear()
    try:

        def handler(request: httpx.Request) -> httpx.Response:
            symbol = request.url.params.get("symbol")
            if symbol == "MRNA":
                return httpx.Response(200, json=QUOTE_PAYLOAD)
            return httpx.Response(200, json=UNCOVERED_PAYLOAD)

        original = httpx.AsyncClient
        monkeypatch.setattr(
            httpx,
            "AsyncClient",
            lambda *a, **kw: original(*a, **{**kw, "transport": httpx.MockTransport(handler)}),
        )

        result = await update_finnhub_quotes(db, ["MRNA", "PFE"])

        assert result["inserted"] == 1
        assert result["uncovered"] == 1

        stored = (await db.execute(select(StockPrice))).scalars().all()
        assert len(stored) == 1
        assert stored[0].close == 261.74
        assert stored[0].source == "finnhub"
    finally:
        get_settings.cache_clear()


@pytest.mark.asyncio
async def test_update_is_idempotent_within_a_trading_day(db, seeded_stocks, monkeypatch):
    """Refreshing every 60 seconds must update one row, not add 1440 a day."""
    monkeypatch.setenv("FINNHUB_API_KEY", "test-key")
    monkeypatch.setattr("app.integrations.finnhub.REQUEST_DELAY_SECONDS", 0)
    get_settings.cache_clear()
    try:
        original = httpx.AsyncClient
        monkeypatch.setattr(
            httpx,
            "AsyncClient",
            lambda *a, **kw: original(
                *a,
                **{
                    **kw,
                    "transport": httpx.MockTransport(
                        lambda request: httpx.Response(200, json=QUOTE_PAYLOAD)
                    ),
                },
            ),
        )

        await update_finnhub_quotes(db, ["MRNA"])
        second = await update_finnhub_quotes(db, ["MRNA"])

        assert second["inserted"] == 0
        assert second["updated"] == 1
        assert (await db.execute(select(func.count(StockPrice.id)))).scalar_one() == 1
    finally:
        get_settings.cache_clear()


@pytest.mark.asyncio
async def test_update_without_a_key_is_a_no_op(db, seeded_stocks, monkeypatch):
    monkeypatch.delenv("FINNHUB_API_KEY", raising=False)
    get_settings.cache_clear()
    try:
        assert await update_finnhub_quotes(db, ["MRNA"]) == {
            "inserted": 0,
            "updated": 0,
            "uncovered": 0,
        }
    finally:
        get_settings.cache_clear()
