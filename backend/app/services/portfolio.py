"""Paper-trading portfolio: positions, valuation, and signal-driven simulation.

The trade log is the source of truth. Positions and average cost are derived by
replaying trades, which keeps the model honest — there is no denormalised
position row that can drift out of sync with its history.

Realised P&L uses average-cost accounting: a sale books
``(fill price - average cost) * quantity`` and leaves the average cost of the
remaining shares unchanged.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    NewsArticle,
    Portfolio,
    SentimentScore,
    Stock,
    StockPrice,
    Trade,
    TradeSide,
)

logger = logging.getLogger(__name__)


class InsufficientFunds(Exception):
    """Raised when a buy costs more than the portfolio's available cash."""


class InsufficientShares(Exception):
    """Raised when a sell exceeds the shares currently held."""


@dataclass
class Position:
    """Derived holding for one ticker."""

    ticker: str
    quantity: float = 0.0
    average_cost: float = 0.0
    realised_pnl: float = 0.0

    @property
    def cost_basis(self) -> float:
        return self.quantity * self.average_cost


@dataclass
class Valuation:
    """A portfolio's worth at a point in time."""

    cash: float
    positions_value: float
    total_value: float
    starting_cash: float
    realised_pnl: float
    unrealised_pnl: float
    # Market value per quote currency. Nothing is FX-converted, so this is the
    # only trustworthy view once a portfolio spans regions.
    positions_by_currency: dict[str, float] = field(default_factory=dict)

    @property
    def mixed_currency(self) -> bool:
        """True when the single total sums across currencies and is meaningless."""
        return len(self.positions_by_currency) > 1

    @property
    def total_return_pct(self) -> float | None:
        if self.starting_cash <= 0:
            return None
        return (self.total_value - self.starting_cash) / self.starting_cash * 100

    def as_dict(self) -> dict:
        return {
            "cash": round(self.cash, 2),
            "positions_value": round(self.positions_value, 2),
            "total_value": round(self.total_value, 2),
            "starting_cash": round(self.starting_cash, 2),
            "realised_pnl": round(self.realised_pnl, 2),
            "unrealised_pnl": round(self.unrealised_pnl, 2),
            "total_return_pct": (
                round(self.total_return_pct, 2) if self.total_return_pct is not None else None
            ),
            "positions_by_currency": self.positions_by_currency,
            "mixed_currency": self.mixed_currency,
        }


@dataclass
class SimulationResult:
    """Outcome of replaying a sentiment strategy over historical data."""

    trades_executed: int = 0
    signals_seen: int = 0
    signals_skipped: int = 0
    reasons: dict[str, int] = field(default_factory=dict)

    def skip(self, reason: str) -> None:
        self.signals_skipped += 1
        self.reasons[reason] = self.reasons.get(reason, 0) + 1

    def as_dict(self) -> dict:
        return {
            "trades_executed": self.trades_executed,
            "signals_seen": self.signals_seen,
            "signals_skipped": self.signals_skipped,
            "skip_reasons": self.reasons,
        }


def replay(trades: list[tuple[str, str, float, float]]) -> dict[str, Position]:
    """Build positions from ``(ticker, side, quantity, price)`` tuples in order."""
    positions: dict[str, Position] = {}
    for ticker, side, quantity, price in trades:
        position = positions.setdefault(ticker, Position(ticker=ticker))
        if side == TradeSide.BUY.value:
            total_cost = position.cost_basis + quantity * price
            position.quantity += quantity
            position.average_cost = total_cost / position.quantity if position.quantity else 0.0
        else:
            position.realised_pnl += (price - position.average_cost) * quantity
            position.quantity -= quantity
            if position.quantity <= 1e-9:
                # Fully closed: reset so a re-entry starts from a clean basis.
                position.quantity = 0.0
                position.average_cost = 0.0
    return positions


async def get_positions(db: AsyncSession, portfolio_id: int) -> dict[str, Position]:
    """Current holdings for a portfolio, derived from its trade log."""
    rows = (
        await db.execute(
            select(Stock.ticker, Trade.side, Trade.quantity, Trade.price)
            .join(Stock, Stock.id == Trade.ticker_id)
            .where(Trade.portfolio_id == portfolio_id)
            .order_by(Trade.executed_at, Trade.id)
        )
    ).all()
    return replay([(row[0], row[1], row[2], row[3]) for row in rows])


async def latest_prices(db: AsyncSession, tickers: list[str]) -> dict[str, float]:
    """Most recent close per ticker. Tickers without price history are omitted."""
    if not tickers:
        return {}
    rows = (
        await db.execute(
            select(Stock.ticker, StockPrice.close, StockPrice.price_date)
            .join(Stock, Stock.id == StockPrice.ticker_id)
            .where(Stock.ticker.in_(tickers))
            .order_by(Stock.ticker, StockPrice.price_date.desc())
        )
    ).all()
    prices: dict[str, float] = {}
    for ticker, close, _ in rows:
        prices.setdefault(ticker, close)  # first row per ticker is the newest
    return prices


async def _currencies_for(db: AsyncSession, tickers: list[str]) -> dict[str, str]:
    """Quote currency per ticker, so a multi-region portfolio can be honest."""
    if not tickers:
        return {}
    rows = (
        await db.execute(
            select(Stock.ticker, Stock.currency).where(Stock.ticker.in_(tickers))
        )
    ).all()
    return {ticker: (currency or "USD") for ticker, currency in rows}


async def value_portfolio(db: AsyncSession, portfolio: Portfolio) -> tuple[Valuation, list[dict]]:
    """Value a portfolio at the latest known prices.

    Holdings with no price history are valued at cost rather than dropped, so
    the total stays meaningful before prices are backfilled.

    **Totals are not FX-converted.** With a multi-region universe a portfolio
    can hold JPY, EUR and USD lines at once, and adding those numbers together
    produces a figure that means nothing. The per-currency breakdown on the
    valuation is the honest view; ``mixed_currency`` flags when the single
    total should not be trusted. Converting properly needs an FX rate source,
    which this platform does not have yet.
    """
    positions = await get_positions(db, portfolio.id)
    open_positions = {t: p for t, p in positions.items() if p.quantity > 0}
    prices = await latest_prices(db, list(open_positions))
    currencies = await _currencies_for(db, list(open_positions))

    positions_value = 0.0
    unrealised = 0.0
    rows: list[dict] = []
    by_currency: dict[str, float] = {}

    for ticker, position in sorted(open_positions.items()):
        price = prices.get(ticker)
        market_value = position.quantity * (price if price is not None else position.average_cost)
        position_unrealised = (
            (price - position.average_cost) * position.quantity if price is not None else 0.0
        )
        positions_value += market_value
        unrealised += position_unrealised
        currency = currencies.get(ticker, "USD")
        by_currency[currency] = round(by_currency.get(currency, 0.0) + market_value, 2)
        rows.append(
            {
                "ticker": ticker,
                "quantity": round(position.quantity, 4),
                "average_cost": round(position.average_cost, 4),
                "last_price": round(price, 4) if price is not None else None,
                "market_value": round(market_value, 2),
                "unrealised_pnl": round(position_unrealised, 2),
                "priced": price is not None,
                "currency": currency,
            }
        )

    realised = sum(position.realised_pnl for position in positions.values())
    valuation = Valuation(
        cash=portfolio.cash,
        positions_value=positions_value,
        total_value=portfolio.cash + positions_value,
        starting_cash=portfolio.starting_cash,
        realised_pnl=realised,
        unrealised_pnl=unrealised,
        positions_by_currency=by_currency,
    )
    return valuation, rows


async def execute_trade(
    db: AsyncSession,
    portfolio: Portfolio,
    stock: Stock,
    side: TradeSide,
    quantity: float,
    price: float,
    rationale: str = "manual",
    executed_at: datetime | None = None,
    stop: float | None = None,
    target: float | None = None,
    setup: str | None = None,
) -> Trade:
    """Record a fill, adjusting cash. Raises on insufficient funds or shares.

    ``stop`` and ``target`` belong on the opening trade. They are what the exit
    monitor acts on, and recording them as numbers rather than inside the
    rationale is the difference between a plan the system can honour and one it
    can only quote back afterwards.
    """
    if quantity <= 0:
        raise ValueError("quantity must be positive")
    if price <= 0:
        raise ValueError("price must be positive")

    cost = quantity * price
    if side is TradeSide.BUY:
        if cost > portfolio.cash + 1e-9:
            raise InsufficientFunds(
                f"{stock.ticker}: need {cost:,.2f} but cash is {portfolio.cash:,.2f}"
            )
        portfolio.cash -= cost
    else:
        positions = await get_positions(db, portfolio.id)
        held = positions.get(stock.ticker, Position(ticker=stock.ticker)).quantity
        if quantity > held + 1e-9:
            raise InsufficientShares(f"{stock.ticker}: holding {held:g}, tried to sell {quantity:g}")
        portfolio.cash += cost

    trade = Trade(
        portfolio_id=portfolio.id,
        ticker_id=stock.id,
        side=side.value,
        quantity=quantity,
        price=price,
        rationale=rationale,
        stop=stop,
        target=target,
        setup=setup,
    )
    if executed_at is not None:
        trade.executed_at = executed_at
    db.add(trade)
    await db.flush()
    return trade


def _as_utc(value: datetime) -> datetime:
    """Normalise a database timestamp to aware UTC.

    PostgreSQL hands back aware datetimes; SQLite (tests, local dev) hands back
    naive ones. The simulation compares timestamps constantly, so every value
    crossing the database boundary goes through here.
    """
    return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)


async def _price_on_or_after(
    db: AsyncSession, ticker_id: int, when: datetime
) -> tuple[float, datetime] | None:
    """First close at or after ``when`` — the fill a signal could realistically get."""
    row = (
        await db.execute(
            select(StockPrice.close, StockPrice.price_date)
            .where(StockPrice.ticker_id == ticker_id, StockPrice.price_date >= when)
            .order_by(StockPrice.price_date)
            .limit(1)
        )
    ).first()
    return (row[0], _as_utc(row[1])) if row else None


async def simulate_sentiment_strategy(
    db: AsyncSession,
    portfolio: Portfolio,
    days: int = 180,
    min_score: float = 0.5,
    min_confidence: float = 0.0,
    position_size_pct: float = 10.0,
    hold_days: int = 5,
) -> SimulationResult:
    """Replay a simple strategy over stored news and prices.

    The rule: on each positive article above ``min_score``, buy
    ``position_size_pct`` of the portfolio's *starting* cash at the next
    available close, then sell the whole position ``hold_days`` later. Negative
    articles close any open position early.

    Trades are recorded with their historical timestamps, so the resulting
    portfolio can be valued and inspected exactly like a hand-traded one. This
    is a teaching tool for the sentiment signal, not a production backtester:
    fills are close-to-close with no slippage, costs, or shorting.
    """
    result = SimulationResult()
    since = datetime.now(timezone.utc) - timedelta(days=days)
    budget = portfolio.starting_cash * (position_size_pct / 100.0)

    signals = (
        await db.execute(
            select(
                NewsArticle.ticker_id,
                Stock.ticker,
                SentimentScore.sentiment,
                SentimentScore.score,
                SentimentScore.confidence,
                NewsArticle.published_at,
            )
            .join(SentimentScore, SentimentScore.article_id == NewsArticle.id)
            .join(Stock, Stock.id == NewsArticle.ticker_id)
            .where(NewsArticle.published_at >= since)
            .order_by(NewsArticle.published_at)
        )
    ).all()

    # Ticker -> (quantity, sell-after timestamp) for positions this run opened.
    open_lots: dict[str, tuple[float, datetime]] = {}
    stocks = {
        stock.ticker: stock
        for stock in (await db.execute(select(Stock))).scalars()
    }

    async def close_position(ticker: str, when: datetime, reason: str) -> None:
        quantity, _ = open_lots.pop(ticker)
        stock = stocks[ticker]
        fill = await _price_on_or_after(db, stock.id, when)
        if fill is None:
            # No price to exit on; put the lot back so a later signal can close it.
            open_lots[ticker] = (quantity, when)
            return
        await execute_trade(
            db, portfolio, stock, TradeSide.SELL, quantity, fill[0], reason, fill[1]
        )
        result.trades_executed += 1

    for ticker_id, ticker, sentiment, score, confidence, published_at in signals:
        result.signals_seen += 1
        published_at = _as_utc(published_at)

        # Time-based exits for lots whose holding period elapsed before this signal.
        for held_ticker, (_, exit_at) in list(open_lots.items()):
            if exit_at <= published_at:
                await close_position(held_ticker, exit_at, "simulated exit: holding period")

        if confidence < min_confidence:
            result.skip("below_min_confidence")
            continue

        if sentiment == "negative" and ticker in open_lots:
            await close_position(ticker, published_at, "simulated exit: negative news")
            continue

        if sentiment != "positive" or score < min_score:
            result.skip("no_signal")
            continue
        if ticker in open_lots:
            result.skip("already_held")
            continue

        fill = await _price_on_or_after(db, ticker_id, published_at)
        if fill is None:
            result.skip("no_price_data")
            continue

        price, price_date = fill
        quantity = budget / price
        try:
            await execute_trade(
                db,
                portfolio,
                stocks[ticker],
                TradeSide.BUY,
                quantity,
                price,
                f"simulated entry: {sentiment} {score:+.2f}",
                price_date,
            )
        except InsufficientFunds:
            result.skip("insufficient_cash")
            continue

        result.trades_executed += 1
        open_lots[ticker] = (quantity, price_date + timedelta(days=hold_days))

    # Close whatever is still open at the last price we have.
    for ticker, (_, exit_at) in list(open_lots.items()):
        await close_position(ticker, exit_at, "simulated exit: end of window")

    await db.commit()
    logger.info("Simulation for portfolio %s: %s", portfolio.id, result.as_dict())
    return result
