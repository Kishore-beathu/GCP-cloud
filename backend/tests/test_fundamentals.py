"""Market cap, earnings surprise, and analyst opinion movement.

The factors with the strongest published evidence behind them, and the ones
this platform could not see at all. What matters most here is not that they
are computed but that they are computed *honestly*: a surprise against a
negative estimate, an opinion score for a symbol nobody covers, and a
backtest that can only see what had been reported at the time.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.integrations.finnhub import _surprise_pct
from app.models import AnalystTrend, EarningsReport, Stock
from app.services import fundamentals


def _trend(**counts) -> AnalystTrend:
    return AnalystTrend(
        ticker_id=1,
        period=counts.pop("period", datetime.now(timezone.utc)),
        strong_buy=counts.get("strong_buy", 0),
        buy=counts.get("buy", 0),
        hold=counts.get("hold", 0),
        sell=counts.get("sell", 0),
        strong_sell=counts.get("strong_sell", 0),
    )


# --- Surprise arithmetic ------------------------------------------------------


def test_surprise_is_a_percentage_of_the_estimate():
    assert _surprise_pct(1.2, 1.0) == 20.0
    assert _surprise_pct(0.8, 1.0) == -20.0


def test_a_zero_estimate_has_no_surprise_rather_than_infinity():
    assert _surprise_pct(1.2, 0.0) is None


def test_a_negative_estimate_is_refused_rather_than_inverted():
    """The case that matters most, and the one the naive formula gets backwards.

    A company expected to lose 0.80 and losing only 0.50 has beaten. Divide by
    a negative estimate and the sign flips, so the beat reads as a miss — and
    loss-making companies are most of the small-cap universe this is for.
    """
    assert _surprise_pct(-0.5, -0.8) is None
    assert _surprise_pct(-1.0, -0.8) is None


# --- Analyst opinion ----------------------------------------------------------


def test_opinion_weights_strong_calls_double():
    strong = fundamentals._opinion_score(_trend(strong_buy=2))
    ordinary = fundamentals._opinion_score(_trend(buy=2))

    assert strong == 1.0
    assert ordinary == 0.5


def test_an_uncovered_symbol_has_no_opinion_rather_than_a_neutral_one():
    """"Nobody covers this" and "analysts are split" are different facts.

    Scored as zero, an uncovered small cap would sit mid-ranking on no
    information at all — which is where the sentiment pillar's ties came from.
    """
    assert fundamentals._opinion_score(_trend()) is None
    assert fundamentals._opinion_score(_trend(hold=4)) == 0.0


def test_revision_is_the_change_between_months_not_the_level():
    """Sell-side opinion is structurally bullish; only the movement informs."""
    now = datetime.now(timezone.utc)
    stock = Stock(ticker="MU", company_name="Micron", sector="memory")

    improving = fundamentals.summarise(
        stock,
        [],
        [
            _trend(period=now - timedelta(days=30), hold=4),
            _trend(period=now, strong_buy=2, buy=2),
        ],
    )
    deteriorating = fundamentals.summarise(
        stock,
        [],
        [
            _trend(period=now - timedelta(days=30), strong_buy=2, buy=2),
            _trend(period=now, hold=4),
        ],
    )

    assert improving.analyst_revision > 0
    assert deteriorating.analyst_revision < 0
    # Both months are uniformly bullish, so the level says nothing and the
    # change says nothing either.
    flat = fundamentals.summarise(
        stock,
        [],
        [
            _trend(period=now - timedelta(days=30), strong_buy=5),
            _trend(period=now, strong_buy=5),
        ],
    )
    assert flat.analyst_revision == 0.0


def test_a_single_month_gives_no_revision():
    """A change needs two observations, not one."""
    stock = Stock(ticker="MU", company_name="Micron", sector="memory")

    result = fundamentals.summarise(stock, [], [_trend(strong_buy=3)])

    assert result.analyst_revision is None
    assert result.analysts_covering == 3


# --- Summarising --------------------------------------------------------------


def test_the_latest_reported_quarter_is_the_one_that_counts():
    now = datetime.now(timezone.utc)
    stock = Stock(ticker="MU", company_name="Micron", sector="memory")
    reports = [
        EarningsReport(ticker_id=1, period=now - timedelta(days=200), eps_surprise_pct=50.0),
        EarningsReport(ticker_id=1, period=now - timedelta(days=20), eps_surprise_pct=5.0),
    ]

    result = fundamentals.summarise(stock, reports, [], now=now)

    assert result.earnings_surprise_pct == 5.0
    # Drift decays, so how long ago the beat happened is part of the signal.
    assert result.days_since_earnings == 20


def test_a_quarter_with_no_usable_surprise_is_skipped():
    """A report against a negative estimate carries no surprise to rank."""
    now = datetime.now(timezone.utc)
    stock = Stock(ticker="MU", company_name="Micron", sector="memory")
    reports = [
        EarningsReport(ticker_id=1, period=now - timedelta(days=100), eps_surprise_pct=12.0),
        EarningsReport(ticker_id=1, period=now - timedelta(days=10), eps_surprise_pct=None),
    ]

    result = fundamentals.summarise(stock, reports, [], now=now)

    assert result.earnings_surprise_pct == 12.0


# --- Ingest -------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ingest_says_so_when_the_key_is_missing(db, seeded_stocks, monkeypatch):
    """A silent no-op looks exactly like a vendor with no data."""
    monkeypatch.setenv("FINNHUB_API_KEY", "")
    from app.config import get_settings

    get_settings.cache_clear()
    try:
        report = await fundamentals.ingest_fundamentals(db)
    finally:
        get_settings.cache_clear()

    assert report.symbols == 0
    assert "FINNHUB_API_KEY" in report.note


@pytest.mark.asyncio
async def test_market_cap_is_stored_in_units_not_millions(db, seeded_stocks, monkeypatch):
    """The vendor reports millions. A column holding both is a silent trap.

    A filter for "under $500M" would keep every large cap whose value happened
    to be written in millions, and nothing would look wrong.
    """
    from app.config import get_settings
    from app.services import fundamentals as service

    monkeypatch.setenv("FINNHUB_API_KEY", "test-key")
    get_settings.cache_clear()

    async def _profile(client, ticker, key):
        return {"market_cap": 1_234_000_000.0, "shares_outstanding": 500_000_000.0}

    async def _empty(client, ticker, key):
        return []

    monkeypatch.setattr(service, "fetch_profile", _profile)
    monkeypatch.setattr(service, "fetch_earnings", _empty)
    monkeypatch.setattr(service, "fetch_recommendations", _empty)
    monkeypatch.setattr(service, "REQUEST_DELAY_SECONDS", 0)

    try:
        report = await fundamentals.ingest_fundamentals(db, [seeded_stocks[0].ticker])
    finally:
        get_settings.cache_clear()

    assert report.profiles_updated == 1
    await db.refresh(seeded_stocks[0])
    assert seeded_stocks[0].market_cap == 1_234_000_000.0
    assert seeded_stocks[0].fundamentals_at is not None


@pytest.mark.asyncio
async def test_a_symbol_the_vendor_does_not_cover_is_named(db, seeded_stocks, monkeypatch):
    """Non-US listings are absent from the free tier; that is coverage, not failure."""
    from app.config import get_settings
    from app.services import fundamentals as service

    monkeypatch.setenv("FINNHUB_API_KEY", "test-key")
    get_settings.cache_clear()

    async def _nothing(client, ticker, key):
        return None

    async def _empty(client, ticker, key):
        return []

    monkeypatch.setattr(service, "fetch_profile", _nothing)
    monkeypatch.setattr(service, "fetch_earnings", _empty)
    monkeypatch.setattr(service, "fetch_recommendations", _empty)
    monkeypatch.setattr(service, "REQUEST_DELAY_SECONDS", 0)

    try:
        report = await fundamentals.ingest_fundamentals(db, [seeded_stocks[0].ticker])
    finally:
        get_settings.cache_clear()

    assert report.uncovered == [seeded_stocks[0].ticker]
    assert report.profiles_updated == 0
