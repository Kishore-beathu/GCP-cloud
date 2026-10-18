"""The shortlist: positive news joined to a live setup.

Reading three screens and intersecting them by eye is where the mistakes come
from — a headline acted on with no stop defined, a setup taken without noticing
the company reports tomorrow. These tests pin the join, and pin harder on the
empty cases: "no good news", "good news but no setup" and "setups but the
market is shut" are three different situations that all render as an empty
list, and telling them apart is most of this endpoint's value.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.models import CatalystEvent, NewsArticle, SentimentScore, Stock, StockPrice
from app.services import setups
from tests.test_setups import _dip_and_rip_session, now_ending


async def _add_news(db, stock, headline, score, hours_ago=1, source="finnhub"):
    article = NewsArticle(
        ticker_id=stock.id,
        headline=headline,
        url=f"https://example.com/{abs(hash(headline)) % 10**8}",
        source=source,
        published_at=datetime.now(timezone.utc) - timedelta(hours=hours_ago),
    )
    db.add(article)
    await db.flush()
    db.add(
        SentimentScore(
            article_id=article.id,
            sentiment="positive" if score > 0 else "negative",
            score=score,
            confidence=0.8,
            event_type="clinical_trial",
            event_confidence=0.8,
            model_version="lexicon-v4",
        )
    )
    await db.commit()
    return article


async def _add_prior_close(db, stock, close=100.0):
    db.add(
        StockPrice(
            ticker_id=stock.id,
            close=close,
            price_date=datetime.now(timezone.utc) - timedelta(days=1),
            source="test",
        )
    )
    await db.commit()


@pytest.fixture
def live_session(monkeypatch):
    """Every scanned symbol returns a session containing a long setup."""

    async def _session(symbol, window):
        return now_ending(_dip_and_rip_session())

    monkeypatch.setattr("app.routers.setups.fetch_intraday", _session)


@pytest.mark.asyncio
async def test_a_symbol_with_news_and_a_setup_is_listed(
    client, db, seeded_stocks, live_session
):
    stock = seeded_stocks[0]
    await _add_prior_close(db, stock)
    await _add_news(db, stock, "Positive topline Phase 3 results met primary endpoint", 0.95)

    body = (await client.get(f"/shortlist?sector={stock.sector}")).json()

    assert body["matched"] == 1
    row = body["rows"][0]
    assert row["ticker"] == stock.ticker
    assert row["company_name"] == stock.company_name
    assert row["setup"]["direction"] == "long"
    assert row["setup"]["reward_risk"] >= setups.MIN_REWARD_RISK
    assert row["news"]["best_score"] == 0.95
    assert row["news"]["headlines"][0]["headline"].startswith("Positive topline")


@pytest.mark.asyncio
async def test_a_setup_without_news_is_not_listed(client, db, seeded_stocks, live_session):
    """The whole point is the join. A setup alone is what /setups is for."""
    stock = seeded_stocks[0]
    await _add_prior_close(db, stock)

    body = (await client.get(f"/shortlist?sector={stock.sector}")).json()

    assert body["matched"] == 0
    assert body["news_symbols"] == 0
    # And it says why, rather than returning a bare empty list.
    assert "nothing to scan" in body["note"]


@pytest.mark.asyncio
async def test_news_below_the_threshold_does_not_qualify(
    client, db, seeded_stocks, live_session
):
    """"Mildly good-sounding" is not a catalyst."""
    stock = seeded_stocks[0]
    await _add_prior_close(db, stock)
    await _add_news(db, stock, "Company announces partnership expansion", 0.3)

    body = (await client.get(f"/shortlist?sector={stock.sector}")).json()
    assert body["matched"] == 0

    widened = (
        await client.get(f"/shortlist?sector={stock.sector}&min_score=0.2")
    ).json()
    assert widened["matched"] == 1


@pytest.mark.asyncio
async def test_stale_news_falls_outside_the_window(client, db, seeded_stocks, live_session):
    stock = seeded_stocks[0]
    await _add_prior_close(db, stock)
    await _add_news(db, stock, "Positive topline results", 0.9, hours_ago=48)

    body = (await client.get(f"/shortlist?sector={stock.sector}")).json()
    assert body["matched"] == 0

    widened = (await client.get(f"/shortlist?sector={stock.sector}&hours=72")).json()
    assert widened["matched"] == 1


@pytest.mark.asyncio
async def test_good_news_with_no_setup_is_distinguishable_from_no_news(
    client, db, seeded_stocks, monkeypatch
):
    """The two empty lists that look identical and mean opposite things.

    One says the scan found nothing worth trading; the other says the news
    pipeline is not delivering. Acting on the first is patience; acting on the
    second is waiting for a feed that will never arrive.
    """
    stock = seeded_stocks[0]
    await _add_prior_close(db, stock)
    await _add_news(db, stock, "Positive topline results met primary endpoint", 0.9)

    async def _flat(symbol, window):
        # Only the opening bars: nothing to reclaim, so no setup fires.
        return now_ending(_dip_and_rip_session()[:3])

    monkeypatch.setattr("app.routers.setups.fetch_intraday", _flat)

    body = (await client.get(f"/shortlist?sector={stock.sector}")).json()

    assert body["matched"] == 0
    # News arrived; the chart simply did not offer an entry.
    assert body["news_symbols"] == 1
    assert body["signals_found"] == 0


@pytest.mark.asyncio
async def test_an_upcoming_catalyst_travels_with_the_row(
    client, db, seeded_stocks, live_session
):
    """Shown because it cuts both ways, and only the trader can say which."""
    stock = seeded_stocks[0]
    await _add_prior_close(db, stock)
    await _add_news(db, stock, "Positive topline results met primary endpoint", 0.9)
    db.add(
        CatalystEvent(
            ticker_id=stock.id,
            kind="earnings",
            expected_at=datetime.now(timezone.utc) + timedelta(days=1),
            confidence="confirmed",
            title=f"{stock.ticker} Q3 earnings",
            source="finnhub",
        )
    )
    await db.commit()

    row = (await client.get(f"/shortlist?sector={stock.sector}")).json()["rows"][0]

    assert row["catalysts"][0]["kind"] == "earnings"
    assert row["catalysts"][0]["days_away"] == 1


@pytest.mark.asyncio
async def test_rows_are_ordered_by_news_not_by_setup(client, db, live_session):
    """The setups carry no measured hit rate, so ranking by them would invent one."""
    weak = Stock(ticker="AAA", company_name="Weaker news", sector="clinical_stage")
    strong = Stock(ticker="BBB", company_name="Stronger news", sector="clinical_stage")
    db.add_all([weak, strong])
    await db.commit()
    for stock in (weak, strong):
        await db.refresh(stock)
        await _add_prior_close(db, stock)

    await _add_news(db, weak, "Company reports encouraging early data", 0.6)
    await _add_news(db, strong, "Primary endpoint met with statistical significance", 0.98)

    rows = (await client.get("/shortlist?sector=clinical_stage")).json()["rows"]

    assert [row["ticker"] for row in rows] == ["BBB", "AAA"]


@pytest.mark.asyncio
async def test_the_response_refuses_to_imply_a_forecast(client, db, seeded_stocks, live_session):
    """The question this answers invites a prediction; the answer must not give one."""
    stock = seeded_stocks[0]
    await _add_prior_close(db, stock)
    await _add_news(db, stock, "Positive topline results met primary endpoint", 0.9)

    body = (await client.get(f"/shortlist?sector={stock.sector}")).json()

    assert "Not a forecast" in body["caveat"]
    assert "no measured hit rate" in body["caveat"]


@pytest.mark.asyncio
async def test_an_unknown_filter_is_rejected_rather_than_matching_nothing(
    client, seeded_stocks
):
    assert (await client.get("/shortlist?sector=nonsense")).status_code == 422
    assert (await client.get("/shortlist?group=nonsense")).status_code == 422
