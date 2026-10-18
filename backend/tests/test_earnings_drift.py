"""The earnings-surprise strategy, traded on paper with costs charged.

This is the one factor `scoring.validate()` found evidence for — t +4.26 over
twelve periods. Everything measured about the tests below follows from that:
the strategy holds that factor alone, refuses to guess at a missing surprise,
and charges what trading it would actually cost.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.models import EarningsReport, Portfolio, StockPrice
from app.services.portfolio import get_positions

pytestmark = pytest.mark.asyncio


async def _with_earnings(db, stock, *, surprise: float, price: float):
    """Give a symbol a reported quarter and a price to trade at.

    eps_surprise_pct is stored rather than derived on read — the denominator
    needs care when an estimate is zero or negative — so the fixture sets it
    the way the ingest does.
    """
    db.add(
        EarningsReport(
            ticker_id=stock.id,
            period=datetime(2026, 6, 30, tzinfo=timezone.utc),
            reported_at=datetime(2026, 8, 1, tzinfo=timezone.utc),
            eps_actual=1.0 + surprise / 100.0,
            eps_estimate=1.0,
            eps_surprise_pct=surprise,
        )
    )
    db.add(
        StockPrice(
            ticker_id=stock.id,
            close=price,
            price_date=datetime.now(timezone.utc) - timedelta(days=1),
            source="test",
        )
    )
    await db.flush()


async def _portfolio(db, cash: float = 10_000.0) -> Portfolio:
    portfolio = Portfolio(name="Drift", starting_cash=cash, cash=cash)
    db.add(portfolio)
    await db.flush()
    return portfolio


async def test_the_biggest_surprise_is_selected_first(db, seeded_stocks):
    from app.services.earnings_drift import select_candidates

    await _with_earnings(db, seeded_stocks[0], surprise=50.0, price=50.0)
    await _with_earnings(db, seeded_stocks[1], surprise=5.0, price=50.0)
    await db.commit()

    candidates = await select_candidates(db, top_n=5)

    assert [c.ticker for c in candidates] == [
        seeded_stocks[0].ticker,
        seeded_stocks[1].ticker,
    ]
    assert candidates[0].surprise_pct > candidates[1].surprise_pct


async def test_a_symbol_without_a_reported_quarter_is_excluded(db, seeded_stocks):
    """The composite score imputes a missing input at the median, deliberately.

    Here the factor *is* the strategy, so imputing would fill the portfolio
    with companies that never reported — ranked on a number invented for them.
    """
    from app.services.earnings_drift import select_candidates

    await _with_earnings(db, seeded_stocks[0], surprise=20.0, price=50.0)
    await db.commit()

    candidates = await select_candidates(db, top_n=10)

    assert [c.ticker for c in candidates] == [seeded_stocks[0].ticker]


async def test_a_rebalance_buys_the_selection_and_charges_for_it(db, seeded_stocks):
    from app.services.earnings_drift import rebalance

    await _with_earnings(db, seeded_stocks[0], surprise=50.0, price=100.0)
    portfolio = await _portfolio(db)
    await db.commit()

    report = await rebalance(
        db, portfolio.id, top_n=1, commission=1.0, slippage_bps=10.0
    )

    assert report.opened[0]["ticker"] == seeded_stocks[0].ticker
    # Bought at the offer, not the mid: 100 * (1 + 10bps) = 100.10.
    assert report.opened[0]["price"] == pytest.approx(100.10)
    assert report.commission_paid == 1.0
    assert report.slippage_paid > 0


async def test_costs_are_charged_rather_than_assumed_away(db, seeded_stocks):
    """A paper record that fills at the mid measures a strategy nobody can buy."""
    from app.services.earnings_drift import rebalance

    await _with_earnings(db, seeded_stocks[0], surprise=50.0, price=100.0)
    portfolio = await _portfolio(db, cash=10_000.0)
    await db.commit()

    free = await rebalance(db, portfolio.id, top_n=1, commission=0.0, slippage_bps=0.0)
    assert free.commission_paid == 0.0
    assert free.slippage_paid == 0.0

    other = await _portfolio(db, cash=10_000.0)
    await db.commit()
    charged = await rebalance(
        db, other.id, top_n=1, commission=2.5, slippage_bps=25.0
    )

    assert charged.commission_paid == 2.5
    assert charged.slippage_paid > 0


async def test_a_name_that_drops_out_is_sold(db, seeded_stocks):
    from app.services.earnings_drift import rebalance

    first, second = seeded_stocks[0], seeded_stocks[1]
    await _with_earnings(db, first, surprise=50.0, price=100.0)
    portfolio = await _portfolio(db)
    await db.commit()

    await rebalance(db, portfolio.id, top_n=1, commission=0.0, slippage_bps=0.0)
    held = await get_positions(db, portfolio.id)
    assert held[first.ticker].quantity > 0

    # A bigger surprise arrives elsewhere; the old name is no longer top 1.
    await _with_earnings(db, second, surprise=200.0, price=100.0)
    await db.commit()

    report = await rebalance(db, portfolio.id, top_n=1, commission=0.0, slippage_bps=0.0)

    assert report.closed[0]["ticker"] == first.ticker
    assert report.opened[0]["ticker"] == second.ticker
    positions = await get_positions(db, portfolio.id)
    assert positions[first.ticker].quantity == 0


async def test_a_name_still_in_the_selection_is_not_churned(db, seeded_stocks):
    """Selling and rebuying the same position pays two commissions for nothing."""
    from app.services.earnings_drift import rebalance

    await _with_earnings(db, seeded_stocks[0], surprise=50.0, price=100.0)
    portfolio = await _portfolio(db)
    await db.commit()

    await rebalance(db, portfolio.id, top_n=1, commission=1.0, slippage_bps=5.0)
    second = await rebalance(db, portfolio.id, top_n=1, commission=1.0, slippage_bps=5.0)

    assert second.held == [seeded_stocks[0].ticker]
    assert second.opened == []
    assert second.closed == []
    assert second.commission_paid == 0.0


async def test_a_name_too_expensive_for_the_budget_is_named(db, seeded_stocks):
    """A silent omission would make the held portfolio look like the intended one."""
    from app.services.earnings_drift import rebalance

    await _with_earnings(db, seeded_stocks[0], surprise=50.0, price=5_000.0)
    portfolio = await _portfolio(db, cash=1_000.0)
    await db.commit()

    report = await rebalance(db, portfolio.id, top_n=1)

    assert report.opened == []
    assert report.skipped[0]["ticker"] == seeded_stocks[0].ticker
    assert "budget" in report.skipped[0]["reason"]


async def test_the_selection_endpoint_states_its_evidence(db, client, seeded_stocks):
    """The number is only worth acting on beside what backs it."""
    await _with_earnings(db, seeded_stocks[0], surprise=50.0, price=50.0)
    await db.commit()

    response = await client.get("/portfolios/strategies/earnings-drift")

    assert response.status_code == 200
    body = response.json()
    assert body["positions"][0]["ticker"] == seeded_stocks[0].ticker
    assert "t +4.26" in body["evidence"]
    # And the caveat, not just the flattering half.
    assert "not settled" in body["evidence"]
