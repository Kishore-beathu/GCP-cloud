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
        assert result == {
            "symbols": 0,
            "inserted": 0,
            "updated": 0,
            "uncovered": 0,
            "uncovered_symbols": [],
        }
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


# --- Intraday ---------------------------------------------------------------
# Short windows are served live and never stored: stock_prices holds one row
# per trading day, and minute bars in that table would redefine "the previous
# close" for the backtester, the valuation and the watchlist change column.

INTRADAY_PAYLOAD = {
    "chart": {
        "result": [
            {
                "meta": {"currency": "USD", "symbol": "PFE"},
                "timestamp": [1786665600, 1786665660, 1786665720],
                "indicators": {"quote": [{"close": [26.10, 26.14, 26.08]}]},
            }
        ],
        "error": None,
    }
}


def test_parse_intraday_reads_bars_oldest_first():
    from app.integrations.yahoo import parse_intraday

    bars = parse_intraday("PFE", INTRADAY_PAYLOAD, None)

    assert [bar.close for bar in bars] == [26.10, 26.14, 26.08]
    assert bars[0].at < bars[-1].at
    assert bars[0].at.tzinfo is timezone.utc


def test_parse_intraday_keeps_only_the_requested_tail():
    """The 1h window asks for a day of minutes and keeps the last 60."""
    from app.integrations.yahoo import parse_intraday

    bars = parse_intraday("PFE", INTRADAY_PAYLOAD, 2)

    assert [bar.close for bar in bars] == [26.14, 26.08]


def test_parse_intraday_converts_london_pence():
    from app.integrations.yahoo import parse_intraday

    payload = {
        "chart": {
            "result": [
                {
                    "timestamp": [1786665600],
                    "indicators": {"quote": [{"close": [12040.0]}]},
                }
            ]
        }
    }

    assert parse_intraday("AZN.L", payload, None)[0].close == 120.40


def test_parse_intraday_drops_null_bars():
    from app.integrations.yahoo import parse_intraday

    payload = {
        "chart": {
            "result": [
                {
                    "timestamp": [1786665600, 1786665660],
                    "indicators": {"quote": [{"close": [None, 26.14]}]},
                }
            ]
        }
    }

    assert [bar.close for bar in parse_intraday("PFE", payload, None)] == [26.14]


@pytest.mark.asyncio
async def test_intraday_endpoint_returns_points(client, seeded_stocks, monkeypatch):
    from app.integrations import yahoo

    yahoo.clear_intraday_cache()
    _mock_yahoo(monkeypatch, lambda request: httpx.Response(200, json=INTRADAY_PAYLOAD))

    body = (await client.get("/stocks/PFE/intraday?window=1h")).json()

    assert body["ticker"] == "PFE"
    assert body["window"] == "1h"
    assert body["interval"] == "1m"
    assert len(body["points"]) == 3
    assert body["points"][0]["close"] == 26.10


@pytest.mark.asyncio
async def test_intraday_endpoint_rejects_an_unknown_window(client, seeded_stocks):
    assert (await client.get("/stocks/PFE/intraday?window=3y")).status_code == 422


@pytest.mark.asyncio
async def test_intraday_endpoint_404s_for_an_untracked_ticker(client, seeded_stocks):
    assert (await client.get("/stocks/NOSUCH/intraday")).status_code == 404


@pytest.mark.asyncio
async def test_intraday_is_cached_between_calls(client, seeded_stocks, monkeypatch):
    """A dashboard re-requests the same window on every click; ask upstream once."""
    from app.integrations import yahoo

    yahoo.clear_intraday_cache()
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        return httpx.Response(200, json=INTRADAY_PAYLOAD)

    _mock_yahoo(monkeypatch, handler)

    await client.get("/stocks/PFE/intraday?window=1d")
    await client.get("/stocks/PFE/intraday?window=1d")

    assert len(calls) == 1

    # A different window is a different question, so it does hit upstream.
    await client.get("/stocks/PFE/intraday?window=1w")
    assert len(calls) == 2

    yahoo.clear_intraday_cache()


@pytest.mark.asyncio
async def test_intraday_reports_upstream_rate_limiting(client, seeded_stocks, monkeypatch):
    from app.integrations import yahoo

    yahoo.clear_intraday_cache()
    _mock_yahoo(monkeypatch, lambda request: httpx.Response(429, text=""))

    assert (await client.get("/stocks/PFE/intraday?window=1h")).status_code == 503
    yahoo.clear_intraday_cache()


@pytest.mark.asyncio
async def test_uncovered_symbols_are_named_not_just_counted(db, seeded_stocks, monkeypatch):
    """A count says eight symbols will never price without saying which eight.

    Without the names, a symbol Yahoo does not recognise at all is
    indistinguishable from one caught in a transient outage, and neither can
    be acted on.
    """
    monkeypatch.setattr("app.integrations.yahoo.REQUEST_DELAY_SECONDS", 0)
    get_settings.cache_clear()
    try:

        def handler(request: httpx.Request) -> httpx.Response:
            if "MRNA" in str(request.url):
                return httpx.Response(200, json=KOREA_PAYLOAD)
            return httpx.Response(404, json=NOT_FOUND_PAYLOAD)

        _mock_yahoo(monkeypatch, handler)

        result = await update_yahoo_prices(db, ["MRNA", "PFE"])

        assert result["uncovered"] == 1
        assert result["uncovered_symbols"] == ["PFE"]
    finally:
        get_settings.cache_clear()


def test_intraday_drops_the_zero_volume_stub_yahoo_appends():
    """The padding bar was being read as the current price.

    Yahoo ends an intraday series with a bar carrying a close and no trades.
    Kept, it became the newest bar: relative volume divided by zero volume and
    read 0.00x, so no volume-confirmed setup could trigger, and its stale close
    was taken as the entry price.
    """
    from app.integrations.yahoo import parse_intraday

    payload = {
        "chart": {
            "result": [
                {
                    "meta": {"currency": "USD", "symbol": "MU"},
                    "timestamp": [1786492800, 1786493100, 1786493400],
                    "indicators": {
                        "quote": [
                            {
                                "open": [100.0, 101.0, 101.5],
                                "high": [100.5, 101.5, 101.5],
                                "low": [99.5, 100.5, 101.5],
                                "close": [100.2, 101.2, 101.5],
                                "volume": [5000, 4000, 0],
                            }
                        ]
                    },
                }
            ]
        }
    }

    bars = parse_intraday("MU", payload, None)

    assert len(bars) == 2
    assert bars[-1].close == 101.2
    assert bars[-1].volume == 4000


def test_intraday_keeps_a_quiet_bar_in_the_middle_of_a_session():
    """An interior zero is a real quiet interval, not padding."""
    from app.integrations.yahoo import parse_intraday

    payload = {
        "chart": {
            "result": [
                {
                    "timestamp": [1786492800, 1786493100, 1786493400],
                    "indicators": {
                        "quote": [
                            {
                                "open": [100.0, 101.0, 101.5],
                                "high": [100.5, 101.5, 102.0],
                                "low": [99.5, 100.5, 101.0],
                                "close": [100.2, 101.2, 101.8],
                                "volume": [5000, 0, 3000],
                            }
                        ]
                    },
                }
            ]
        }
    }

    bars = parse_intraday("MU", payload, None)

    assert len(bars) == 3
    assert bars[1].volume == 0
