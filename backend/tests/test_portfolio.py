"""Portfolio position maths, trading rules, and the sentiment simulation."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.models import Portfolio, StockPrice, TradeSide
from app.services.ingest import RawArticle, store_articles
from app.services.portfolio import (
    InsufficientFunds,
    InsufficientShares,
    execute_trade,
    get_positions,
    latest_prices,
    replay,
    simulate_sentiment_strategy,
    value_portfolio,
)

# --- Pure position maths ----------------------------------------------------


def test_replay_averages_cost_across_buys():
    positions = replay([("MRNA", "buy", 10, 100.0), ("MRNA", "buy", 10, 120.0)])
    position = positions["MRNA"]
    assert position.quantity == 20
    assert position.average_cost == pytest.approx(110.0)


def test_replay_books_realised_pnl_on_sale():
    positions = replay(
        [("MRNA", "buy", 10, 100.0), ("MRNA", "sell", 4, 130.0)]
    )
    position = positions["MRNA"]
    assert position.quantity == 6
    # Average cost is unchanged by a partial sale.
    assert position.average_cost == pytest.approx(100.0)
    assert position.realised_pnl == pytest.approx(120.0)


def test_replay_resets_basis_when_fully_closed():
    positions = replay(
        [("MRNA", "buy", 10, 100.0), ("MRNA", "sell", 10, 90.0), ("MRNA", "buy", 5, 50.0)]
    )
    position = positions["MRNA"]
    assert position.quantity == 5
    assert position.average_cost == pytest.approx(50.0)
    assert position.realised_pnl == pytest.approx(-100.0)


# --- Trading rules ----------------------------------------------------------


async def _portfolio(db, cash: float = 10_000.0) -> Portfolio:
    portfolio = Portfolio(name="Test", starting_cash=cash, cash=cash)
    db.add(portfolio)
    await db.commit()
    await db.refresh(portfolio)
    return portfolio


@pytest.mark.asyncio
async def test_buy_and_sell_move_cash(db, seeded_stocks):
    portfolio = await _portfolio(db)
    mrna = seeded_stocks[0]

    await execute_trade(db, portfolio, mrna, TradeSide.BUY, 10, 100.0)
    assert portfolio.cash == pytest.approx(9_000.0)

    await execute_trade(db, portfolio, mrna, TradeSide.SELL, 4, 150.0)
    assert portfolio.cash == pytest.approx(9_600.0)

    positions = await get_positions(db, portfolio.id)
    assert positions["MRNA"].quantity == 6
    assert positions["MRNA"].realised_pnl == pytest.approx(200.0)


@pytest.mark.asyncio
async def test_buy_beyond_cash_is_rejected(db, seeded_stocks):
    portfolio = await _portfolio(db, cash=500.0)
    with pytest.raises(InsufficientFunds):
        await execute_trade(db, portfolio, seeded_stocks[0], TradeSide.BUY, 10, 100.0)
    assert portfolio.cash == pytest.approx(500.0)


@pytest.mark.asyncio
async def test_sell_beyond_holding_is_rejected(db, seeded_stocks):
    portfolio = await _portfolio(db)
    await execute_trade(db, portfolio, seeded_stocks[0], TradeSide.BUY, 2, 100.0)
    with pytest.raises(InsufficientShares):
        await execute_trade(db, portfolio, seeded_stocks[0], TradeSide.SELL, 5, 100.0)


@pytest.mark.asyncio
async def test_non_positive_quantity_rejected(db, seeded_stocks):
    portfolio = await _portfolio(db)
    with pytest.raises(ValueError):
        await execute_trade(db, portfolio, seeded_stocks[0], TradeSide.BUY, 0, 100.0)


# --- Valuation --------------------------------------------------------------


@pytest.mark.asyncio
async def test_valuation_uses_latest_close(db, seeded_stocks):
    portfolio = await _portfolio(db)
    mrna = seeded_stocks[0]
    now = datetime.now(timezone.utc)
    db.add(StockPrice(ticker_id=mrna.id, close=100.0, price_date=now - timedelta(days=1), source="t"))
    db.add(StockPrice(ticker_id=mrna.id, close=150.0, price_date=now, source="t"))
    await db.commit()

    await execute_trade(db, portfolio, mrna, TradeSide.BUY, 10, 100.0)
    await db.commit()

    valuation, rows = await value_portfolio(db, portfolio)
    assert valuation.cash == pytest.approx(9_000.0)
    assert valuation.positions_value == pytest.approx(1_500.0)
    assert valuation.total_value == pytest.approx(10_500.0)
    assert valuation.unrealised_pnl == pytest.approx(500.0)
    assert valuation.total_return_pct == pytest.approx(5.0)
    assert rows[0]["priced"] is True


@pytest.mark.asyncio
async def test_unpriced_holdings_are_valued_at_cost(db, seeded_stocks):
    portfolio = await _portfolio(db)
    await execute_trade(db, portfolio, seeded_stocks[0], TradeSide.BUY, 10, 100.0)
    await db.commit()

    valuation, rows = await value_portfolio(db, portfolio)
    # No price history: the position holds its cost, so the total is unchanged.
    assert valuation.total_value == pytest.approx(10_000.0)
    assert valuation.unrealised_pnl == pytest.approx(0.0)
    assert rows[0]["priced"] is False
    assert rows[0]["last_price"] is None


@pytest.mark.asyncio
async def test_latest_prices_picks_newest_per_ticker(db, seeded_stocks):
    mrna, pfe = seeded_stocks[0], seeded_stocks[1]
    now = datetime.now(timezone.utc)
    db.add(StockPrice(ticker_id=mrna.id, close=10.0, price_date=now - timedelta(days=2), source="t"))
    db.add(StockPrice(ticker_id=mrna.id, close=20.0, price_date=now, source="t"))
    db.add(StockPrice(ticker_id=pfe.id, close=30.0, price_date=now, source="t"))
    await db.commit()

    prices = await latest_prices(db, ["MRNA", "PFE", "NOSUCH"])
    assert prices == {"MRNA": 20.0, "PFE": 30.0}


# --- Simulation -------------------------------------------------------------


async def _seed_history(db, stock, base_date: datetime, closes: list[float]) -> None:
    for offset, close in enumerate(closes):
        db.add(
            StockPrice(
                ticker_id=stock.id,
                close=close,
                price_date=base_date + timedelta(days=offset),
                source="test",
            )
        )
    await db.commit()


@pytest.mark.asyncio
async def test_simulation_buys_on_positive_news_and_exits_after_hold(db, seeded_stocks):
    mrna = seeded_stocks[0]
    start = datetime.now(timezone.utc) - timedelta(days=30)
    # Rising series so the trade is profitable.
    await _seed_history(db, mrna, start, [100.0 + i * 2 for i in range(30)])

    await store_articles(
        db,
        [
            RawArticle(
                ticker="MRNA",
                headline="FDA approves therapy after priority review",
                url="https://example.com/sim-positive",
                source="test_feed",
                published_at=start + timedelta(days=1),
            )
        ],
    )

    portfolio = await _portfolio(db, cash=10_000.0)
    result = await simulate_sentiment_strategy(db, portfolio, days=60, hold_days=5)

    assert result.signals_seen == 1
    # One entry and one exit.
    assert result.trades_executed == 2

    positions = await get_positions(db, portfolio.id)
    assert positions["MRNA"].quantity == 0  # closed out
    assert positions["MRNA"].realised_pnl > 0  # rising market
    assert portfolio.cash > 10_000.0


@pytest.mark.asyncio
async def test_simulation_skips_without_price_data(db, seeded_stocks):
    start = datetime.now(timezone.utc) - timedelta(days=10)
    await store_articles(
        db,
        [
            RawArticle(
                ticker="MRNA",
                headline="FDA approves therapy after priority review",
                url="https://example.com/sim-nodata",
                source="test_feed",
                published_at=start,
            )
        ],
    )

    portfolio = await _portfolio(db)
    result = await simulate_sentiment_strategy(db, portfolio, days=60)

    assert result.trades_executed == 0
    assert result.reasons.get("no_price_data") == 1
    assert portfolio.cash == pytest.approx(10_000.0)


@pytest.mark.asyncio
async def test_simulation_exits_early_on_negative_news(db, seeded_stocks):
    mrna = seeded_stocks[0]
    start = datetime.now(timezone.utc) - timedelta(days=30)
    await _seed_history(db, mrna, start, [100.0] * 30)

    await store_articles(
        db,
        [
            RawArticle(
                ticker="MRNA",
                headline="FDA approves therapy after priority review",
                url="https://example.com/sim-pos",
                source="test_feed",
                published_at=start + timedelta(days=1),
            ),
            RawArticle(
                ticker="MRNA",
                headline="Company recalls lots after FDA warning letter",
                url="https://example.com/sim-neg",
                source="test_feed",
                published_at=start + timedelta(days=2),
            ),
        ],
    )

    portfolio = await _portfolio(db)
    result = await simulate_sentiment_strategy(db, portfolio, days=60, hold_days=30)

    # Entry on the positive story, exit on the negative one well before hold_days.
    assert result.trades_executed == 2
    trades = await get_positions(db, portfolio.id)
    assert trades["MRNA"].quantity == 0
