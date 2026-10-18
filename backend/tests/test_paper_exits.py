"""Closing paper positions on their recorded stop or target.

The point is not convenience. A log where targets are closed because someone
was watching and stops are missed because they were not is biased, and any hit
rate computed from it measures attention rather than the setup.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.integrations.yahoo import Bar, YahooUnavailable
from app.models import Portfolio, TradeSide
from app.services.portfolio import execute_trade, get_positions

pytestmark = pytest.mark.asyncio


def _bar(close: float, minutes_old: float = 1.0) -> Bar:
    return Bar(
        at=datetime.now(timezone.utc) - timedelta(minutes=minutes_old),
        close=close,
        open=close,
        high=close,
        low=close,
        volume=1000,
    )


async def _portfolio_with_position(
    db, stock, *, entry=100.0, quantity=10, stop=98.0, target=104.0
) -> Portfolio:
    portfolio = Portfolio(name="Paper", starting_cash=10_000.0, cash=10_000.0)
    db.add(portfolio)
    await db.flush()
    await execute_trade(
        db,
        portfolio,
        stock,
        TradeSide.BUY,
        quantity,
        entry,
        rationale="L1 dip and rip",
        stop=stop,
        target=target,
        setup="L1 dip and rip",
    )
    await db.commit()
    return portfolio


async def test_a_breached_stop_closes_the_position(db, seeded_stocks, monkeypatch):
    from app.services import paper_exits

    stock = seeded_stocks[0]
    portfolio = await _portfolio_with_position(db, stock)

    monkeypatch.setattr(paper_exits, "REQUEST_DELAY_SECONDS", 0.0)
    monkeypatch.setattr(
        paper_exits, "fetch_intraday", lambda t, w: _ret([_bar(97.5)])
    )

    report = await paper_exits.check_exits(db, portfolio.id)

    assert len(report.exits) == 1
    assert report.exits[0]["level"] == "stop"
    positions = await get_positions(db, portfolio.id)
    assert positions[stock.ticker].quantity == 0


async def test_the_fill_is_the_observed_price_not_the_planned_level(
    db, seeded_stocks, monkeypatch
):
    """A bar that gapped through the stop did not offer a fill at the stop.

    Recording the level as the fill would credit a price that was never
    available, and every hit rate built on the log would be optimistic by
    exactly the slippage it hid.
    """
    from app.services import paper_exits

    stock = seeded_stocks[0]
    portfolio = await _portfolio_with_position(db, stock, stop=98.0)

    monkeypatch.setattr(paper_exits, "REQUEST_DELAY_SECONDS", 0.0)
    monkeypatch.setattr(
        paper_exits, "fetch_intraday", lambda t, w: _ret([_bar(96.4)])
    )

    report = await paper_exits.check_exits(db, portfolio.id)

    exit_row = report.exits[0]
    assert exit_row["filled"] == 96.4
    assert exit_row["planned"] == 98.0
    # Negative slippage: worse than planned, and visible.
    assert exit_row["slippage"] == pytest.approx(-1.6)
    assert exit_row["pnl"] == pytest.approx((96.4 - 100.0) * 10, abs=0.01)


async def test_a_breached_target_closes_the_position(db, seeded_stocks, monkeypatch):
    from app.services import paper_exits

    stock = seeded_stocks[0]
    portfolio = await _portfolio_with_position(db, stock, target=104.0)

    monkeypatch.setattr(paper_exits, "REQUEST_DELAY_SECONDS", 0.0)
    monkeypatch.setattr(
        paper_exits, "fetch_intraday", lambda t, w: _ret([_bar(104.6)])
    )

    report = await paper_exits.check_exits(db, portfolio.id)

    assert report.exits[0]["level"] == "target"
    assert report.exits[0]["pnl"] == pytest.approx(46.0, abs=0.01)


async def test_a_bar_covering_both_levels_takes_the_stop(
    db, seeded_stocks, monkeypatch
):
    """Assuming the happier of two possible outcomes is how a record flatters itself."""
    from app.services import paper_exits

    stock = seeded_stocks[0]
    # Stop above the target is nonsense as a plan, but it forces both branches
    # to be true at once, which is what the ordering has to resolve.
    portfolio = await _portfolio_with_position(db, stock, stop=105.0, target=104.0)

    monkeypatch.setattr(paper_exits, "REQUEST_DELAY_SECONDS", 0.0)
    monkeypatch.setattr(
        paper_exits, "fetch_intraday", lambda t, w: _ret([_bar(104.5)])
    )

    report = await paper_exits.check_exits(db, portfolio.id)

    assert report.exits[0]["level"] == "stop"


async def test_a_position_between_the_levels_is_left_alone(
    db, seeded_stocks, monkeypatch
):
    from app.services import paper_exits

    stock = seeded_stocks[0]
    portfolio = await _portfolio_with_position(db, stock)

    monkeypatch.setattr(paper_exits, "REQUEST_DELAY_SECONDS", 0.0)
    monkeypatch.setattr(
        paper_exits, "fetch_intraday", lambda t, w: _ret([_bar(101.0)])
    )

    report = await paper_exits.check_exits(db, portfolio.id)

    assert report.exits == []
    positions = await get_positions(db, portfolio.id)
    assert positions[stock.ticker].quantity == 10


async def test_a_stale_bar_does_not_price_an_exit(db, seeded_stocks, monkeypatch):
    """An exit priced off a twenty-minute-old bar is fiction.

    Non-US venues are delayed on this feed, so this is the ordinary case for
    them rather than an edge case.
    """
    from app.services import paper_exits

    stock = seeded_stocks[0]
    portfolio = await _portfolio_with_position(db, stock)

    monkeypatch.setattr(paper_exits, "REQUEST_DELAY_SECONDS", 0.0)
    monkeypatch.setattr(
        paper_exits,
        "fetch_intraday",
        lambda t, w: _ret([_bar(90.0, minutes_old=25.0)]),
    )

    report = await paper_exits.check_exits(db, portfolio.id)

    assert report.exits == []
    assert report.stale == [stock.ticker]
    positions = await get_positions(db, portfolio.id)
    assert positions[stock.ticker].quantity == 10


async def test_a_position_with_no_plan_is_named_not_guessed_at(
    db, seeded_stocks, monkeypatch
):
    """Inventing a stop for a position whose owner set none is not the job."""
    from app.services import paper_exits

    stock = seeded_stocks[0]
    portfolio = await _portfolio_with_position(db, stock, stop=None, target=None)

    monkeypatch.setattr(paper_exits, "REQUEST_DELAY_SECONDS", 0.0)
    monkeypatch.setattr(
        paper_exits, "fetch_intraday", lambda t, w: _ret([_bar(50.0)])
    )

    report = await paper_exits.check_exits(db, portfolio.id)

    assert report.exits == []
    assert report.unplanned == [stock.ticker]


async def test_being_rate_limited_stops_the_sweep(db, seeded_stocks, monkeypatch):
    from app.services import paper_exits

    stock = seeded_stocks[0]
    portfolio = await _portfolio_with_position(db, stock)

    monkeypatch.setattr(paper_exits, "REQUEST_DELAY_SECONDS", 0.0)

    async def _refuse(ticker, window):
        raise YahooUnavailable("HTTP 429 - being rate limited")

    monkeypatch.setattr(paper_exits, "fetch_intraday", _refuse)

    report = await paper_exits.check_exits(db, portfolio.id)

    assert report.exits == []
    assert report.failures == {"YahooUnavailable": 1}


async def test_the_exit_records_the_setup_that_opened_it(
    db, seeded_stocks, monkeypatch
):
    """The log has to be groupable by setup, or the hit rate cannot be counted."""
    from app.services import paper_exits

    stock = seeded_stocks[0]
    portfolio = await _portfolio_with_position(db, stock)

    monkeypatch.setattr(paper_exits, "REQUEST_DELAY_SECONDS", 0.0)
    monkeypatch.setattr(
        paper_exits, "fetch_intraday", lambda t, w: _ret([_bar(104.5)])
    )

    report = await paper_exits.check_exits(db, portfolio.id)

    assert report.exits[0]["setup"] == "L1 dip and rip"


async def test_the_endpoint_reports_the_sweep(db, client, seeded_stocks, monkeypatch):
    from app.services import paper_exits

    stock = seeded_stocks[0]
    portfolio = await _portfolio_with_position(db, stock)

    monkeypatch.setattr(paper_exits, "REQUEST_DELAY_SECONDS", 0.0)
    monkeypatch.setattr(
        paper_exits, "fetch_intraday", lambda t, w: _ret([_bar(97.0)])
    )

    response = await client.post(f"/portfolios/{portfolio.id}/exits/check")

    assert response.status_code == 200
    body = response.json()
    assert body["exits_taken"] == 1
    assert body["exits"][0]["level"] == "stop"


async def _ret(value):
    return value
