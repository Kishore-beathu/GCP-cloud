"""Yahoo chart parsing and the multi-venue traps it has to survive."""

from __future__ import annotations

from datetime import timezone

import httpx
import pytest
from sqlalchemy import func, select

from app.config import get_settings
from app.integrations.yahoo import (
    YahooUnavailable,
    count_unpriced,
    fetch_chart,
    parse_chart,
    update_yahoo_prices,
)
from app.models import StockPrice

# Two sessions of a Korean listing — the case that had no source at all.
KOREA_PAYLOAD = {
    "chart": {
        "result": [
            {
                "meta": {"currency": "KRW", "symbol": "068270.KS"},
                "timestamp": [1786492800, 1786579200],
                "indicators": {
                    "quote": [
                        {
                            "open": [176000.0, 178500.0],
                            "high": [179000.0, 181000.0],
                            "low": [175500.0, 178000.0],
                            "close": [178500.0, 180500.0],
                            "volume": [412000, 388000],
                        }
                    ]
                },
            }
        ],
        "error": None,
    }
}

# London quotes in pence: 12,040 GBp is £120.40, not £12,040.
LONDON_PAYLOAD = {
    "chart": {
        "result": [
            {
                "meta": {"currency": "GBp", "symbol": "AZN.L"},
                "timestamp": [1786492800],
                "indicators": {
                    "quote": [
                        {
                            "open": [11980.0],
                            "high": [12100.0],
                            "low": [11950.0],
                            "close": [12040.0],
                            "volume": [1500000],
                        }
                    ]
                },
            }
        ],
        "error": None,
    }
}

NOT_FOUND_PAYLOAD = {
    "chart": {
        "result": None,
        "error": {"code": "Not Found", "description": "No data found, symbol may be delisted"},
    }
}


def test_parse_chart_reads_a_daily_series():
    quotes = parse_chart("068270.KS", KOREA_PAYLOAD)

    assert len(quotes) == 2
    assert [q.close for q in quotes] == [178500.0, 180500.0]
    assert quotes[0].volume == 412000
    assert quotes[0].trading_day.tzinfo is timezone.utc
    assert quotes[0].trading_day < quotes[1].trading_day  # oldest first


def test_parse_chart_converts_london_pence_to_pounds():
    """Without this AZN.L reads a hundred times AZN and every comparison lies."""
    quote = parse_chart("AZN.L", LONDON_PAYLOAD)[0]

    assert quote.close == 120.40
    assert quote.open == 119.80
    assert quote.high == 121.00


def test_parse_chart_leaves_other_venues_unscaled():
    assert parse_chart("068270.KS", KOREA_PAYLOAD)[0].close == 178500.0


def test_parse_chart_skips_null_bars():
    """Yahoo pads the series with nulls for non-trading sessions."""
    payload = {
        "chart": {
            "result": [
                {
                    "timestamp": [1786492800, 1786579200, 1786665600],
                    "indicators": {
                        "quote": [{"close": [100.0, None, 102.0], "open": [], "volume": []}]
                    },
                }
            ]
        }
    }

    assert [q.close for q in parse_chart("PFE", payload)] == [100.0, 102.0]


def test_parse_chart_handles_an_error_response():
    assert parse_chart("NOSUCH.XX", NOT_FOUND_PAYLOAD) == []
    assert parse_chart("PFE", {}) == []


def _client(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


@pytest.mark.asyncio
async def test_fetch_chart_returns_empty_for_an_unknown_symbol():
    """A 404 is an ordinary outcome for an obscure listing, not a failure."""
    async with _client(lambda request: httpx.Response(404, json=NOT_FOUND_PAYLOAD)) as client:
        assert await fetch_chart(client, "NOSUCH.XX") == []


@pytest.mark.asyncio
async def test_fetch_chart_stops_the_batch_when_rate_limited():
    async with _client(lambda request: httpx.Response(429, text="")) as client:
        with pytest.raises(YahooUnavailable):
            await fetch_chart(client, "PFE")


def _mock_yahoo(monkeypatch, handler) -> None:
    original = httpx.AsyncClient
    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda *a, **kw: original(*a, **{**kw, "transport": httpx.MockTransport(handler)}),
    )


@pytest.mark.asyncio
async def test_update_stores_history_and_counts_uncovered(db, seeded_stocks, monkeypatch):
    monkeypatch.setattr("app.integrations.yahoo.REQUEST_DELAY_SECONDS", 0)
    get_settings.cache_clear()
    try:

        def handler(request: httpx.Request) -> httpx.Response:
            if str(request.url).endswith("/MRNA") or "MRNA" in str(request.url):
                return httpx.Response(200, json=KOREA_PAYLOAD)
            return httpx.Response(404, json=NOT_FOUND_PAYLOAD)

        _mock_yahoo(monkeypatch, handler)

        result = await update_yahoo_prices(db, ["MRNA", "PFE"])

        assert result["symbols"] == 2
        assert result["inserted"] == 2  # two sessions for the covered symbol
        assert result["uncovered"] == 1
        assert (await db.execute(select(func.count(StockPrice.id)))).scalar_one() == 2
        assert (await db.execute(select(StockPrice.source).limit(1))).scalar_one() == "yahoo"
    finally:
        get_settings.cache_clear()


@pytest.mark.asyncio
async def test_only_missing_skips_symbols_that_already_have_prices(
    db, seeded_stocks, monkeypatch
):
    """The point of the endpoint: fill the dashes, don't re-fetch what works."""
    from datetime import datetime

    monkeypatch.setattr("app.integrations.yahoo.REQUEST_DELAY_SECONDS", 0)
    get_settings.cache_clear()
    try:
        db.add(
            StockPrice(
                ticker_id=seeded_stocks[0].id,
                close=1.0,
                price_date=datetime.now(timezone.utc),
                source="finnhub",
            )
        )
        await db.commit()

        requested: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            requested.append(str(request.url).split("/chart/")[1].split("?")[0])
            return httpx.Response(404, json=NOT_FOUND_PAYLOAD)

        _mock_yahoo(monkeypatch, handler)

        result = await update_yahoo_prices(db, only_missing=True)

        assert result["symbols"] == 1
        assert requested == ["PFE"]  # MRNA already had a price
    finally:
        get_settings.cache_clear()


@pytest.mark.asyncio
async def test_disabled_setting_makes_it_a_no_op(db, seeded_stocks, monkeypatch):
    monkeypatch.setenv("YAHOO_PRICES_ENABLED", "false")
    get_settings.cache_clear()
    try:
        result = await update_yahoo_prices(db, ["MRNA"])
        assert result == {"symbols": 0, "inserted": 0, "updated": 0, "uncovered": 0}
    finally:
        get_settings.cache_clear()


@pytest.mark.asyncio
async def test_count_unpriced_tracks_progress(db, seeded_stocks):
    from datetime import datetime

    assert await count_unpriced(db) == 2

    db.add(
        StockPrice(
            ticker_id=seeded_stocks[0].id,
            close=1.0,
            price_date=datetime.now(timezone.utc),
            source="yahoo",
        )
    )
    await db.commit()

    assert await count_unpriced(db) == 1
