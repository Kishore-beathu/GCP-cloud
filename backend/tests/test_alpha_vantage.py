"""Alpha Vantage parsing and price upserts."""

from __future__ import annotations

from datetime import timezone

import httpx
import pytest
from sqlalchemy import func, select

from app.config import get_settings
from app.integrations.alpha_vantage import (
    AlphaVantageThrottled,
    backfill_daily,
    parse_daily_series,
    parse_global_quote,
    upsert_quotes,
)
from app.models import StockPrice

GLOBAL_QUOTE_PAYLOAD = {
    "Global Quote": {
        "01. symbol": "MRNA",
        "02. open": "142.10",
        "03. high": "146.80",
        "04. low": "141.55",
        "05. price": "145.23",
        "06. volume": "3456789",
        "07. latest trading day": "2026-08-07",
        "08. previous close": "141.70",
        "09. change": "3.53",
        "10. change percent": "2.4912%",
    }
}

DAILY_PAYLOAD = {
    "Meta Data": {"2. Symbol": "MRNA"},
    "Time Series (Daily)": {
        "2026-08-07": {
            "1. open": "142.10", "2. high": "146.80", "3. low": "141.55",
            "4. close": "145.23", "5. volume": "3456789",
        },
        "2026-08-06": {
            "1. open": "140.00", "2. high": "142.50", "3. low": "139.20",
            "4. close": "141.70", "5. volume": "2987654",
        },
        "bad-date": {"4. close": "100.0"},
        "2026-08-05": {"4. close": "not-a-number"},
    },
}

THROTTLE_PAYLOAD = {
    "Note": "Thank you for using Alpha Vantage! Our standard API call frequency is 5 calls per minute."
}


def test_parse_global_quote():
    quote = parse_global_quote("MRNA", GLOBAL_QUOTE_PAYLOAD)
    assert quote is not None
    assert quote.close == 145.23
    assert quote.volume == 3456789
    assert quote.trading_day.date().isoformat() == "2026-08-07"
    assert quote.trading_day.tzinfo is timezone.utc


def test_parse_global_quote_empty_payload():
    assert parse_global_quote("MRNA", {}) is None
    assert parse_global_quote("MRNA", {"Global Quote": {}}) is None


def test_parse_global_quote_raises_on_throttle():
    with pytest.raises(AlphaVantageThrottled):
        parse_global_quote("MRNA", THROTTLE_PAYLOAD)


def test_parse_daily_series_sorted_and_junk_skipped():
    quotes = parse_daily_series("MRNA", DAILY_PAYLOAD)
    # bad-date and non-numeric close rows are dropped; the rest sort oldest first.
    assert [q.trading_day.date().isoformat() for q in quotes] == ["2026-08-06", "2026-08-07"]
    assert quotes[-1].close == 145.23


@pytest.mark.asyncio
async def test_upsert_inserts_then_updates(db, seeded_stocks):
    mrna = seeded_stocks[0]
    quotes = parse_daily_series("MRNA", DAILY_PAYLOAD)

    result = await upsert_quotes(db, mrna, quotes)
    assert result == {"inserted": 2, "updated": 0}

    # Same trading day again (e.g. an intraday refresh) updates in place.
    updated_quote = parse_global_quote("MRNA", GLOBAL_QUOTE_PAYLOAD)
    result = await upsert_quotes(db, mrna, [updated_quote])
    assert result == {"inserted": 0, "updated": 1}

    count = (await db.execute(select(func.count(StockPrice.id)))).scalar_one()
    assert count == 2
    row = (
        await db.execute(
            select(StockPrice).order_by(StockPrice.price_date.desc()).limit(1)
        )
    ).scalar_one()
    assert row.close == 145.23
    assert row.source == "alpha_vantage"


@pytest.mark.asyncio
async def test_backfill_requires_known_ticker(db, seeded_stocks, monkeypatch):
    monkeypatch.setenv("ALPHA_VANTAGE_API_KEY", "test-key")
    get_settings.cache_clear()
    try:
        transport = httpx.MockTransport(lambda request: httpx.Response(200, json=DAILY_PAYLOAD))
        original_client = httpx.AsyncClient

        def patched_client(*args, **kwargs):
            kwargs["transport"] = transport
            return original_client(*args, **kwargs)

        monkeypatch.setattr(httpx, "AsyncClient", patched_client)

        with pytest.raises(LookupError):
            await backfill_daily(db, "NOSUCH")

        result = await backfill_daily(db, "mrna")
        assert result == {"inserted": 2, "updated": 0}
    finally:
        get_settings.cache_clear()


@pytest.mark.asyncio
async def test_backfill_skips_without_key(db, seeded_stocks):
    get_settings.cache_clear()
    result = await backfill_daily(db, "MRNA")
    assert result == {"inserted": 0, "updated": 0}

    # An unknown ticker is still a 404-worthy error, key or no key.
    with pytest.raises(LookupError):
        await backfill_daily(db, "NOSUCH")
