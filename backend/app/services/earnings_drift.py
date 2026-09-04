"""The earnings-surprise strategy, as something that can actually be traded.

This is the one ranking in the platform with measured evidence behind it.
`scoring.validate()` over twelve periods put `earnings_surprise_pct` at t +4.26,
positive in eleven of them, with a 3.21-point spread between the top and bottom
deciles over 21 days. Every other strategy measured — the blended score, the
technical pillar, momentum, sentiment — came back indistinguishable from zero.

So this module trades that one factor and nothing else. It is deliberately not
"the score": mixing in factors that measured nothing would dilute the only
signal there is with noise, while making the result impossible to attribute.

**Costs are charged, not ignored.** A monthly rebalance of ten positions is
roughly 240 fills a year, and at retail commissions plus half-spread that is a
material share of a 3.21-point edge. A paper record that skips them measures a
strategy nobody can buy. Both are configurable, and the report states what they
took.

**Selection is point-in-time by construction.** It reads the surprise stored
against each symbol now and ranks today's universe. It does not reconstruct
history — the backtest in `scoring.validate()` does that, and does it properly.
Running this on a schedule is what builds the forward record.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Portfolio, Stock, TradeSide
from app.services.portfolio import execute_trade, get_positions, latest_prices

logger = logging.getLogger(__name__)

SETUP_NAME = "earnings drift"

# How many names to hold. Ten is a compromise: the measured spread is a
# decile statistic, so holding far fewer is no longer the thing that was
# measured, and holding far more spreads a €10,000 account so thin that
# commission dominates every position.
DEFAULT_POSITIONS = 10

# A symbol must have a real surprise figure. Imputing one at the median — which
# the composite score does, deliberately, so a symbol is not punished for a
# missing input — would fill this portfolio with companies that never reported.
# Here the factor *is* the strategy, so absence is disqualifying rather than
# neutral.


@dataclass
class Candidate:
    ticker: str
    company_name: str
    surprise_pct: float
    price: float | None


@dataclass
class RebalanceReport:
    """What one rebalance did, and what it cost."""

    selected: list[str] = field(default_factory=list)
    opened: list[dict] = field(default_factory=list)
    closed: list[dict] = field(default_factory=list)
    held: list[str] = field(default_factory=list)
    # Names that qualified but could not be bought, and why. A silent omission
    # would make the held portfolio look like the intended one.
    skipped: list[dict] = field(default_factory=list)
    commission_paid: float = 0.0
    slippage_paid: float = 0.0
    cash_after: float = 0.0

    def as_dict(self) -> dict:
        return {
            "setup": SETUP_NAME,
            "selected": self.selected,
            "opened": self.opened,
            "closed": self.closed,
            "held": self.held,
            "skipped": self.skipped,
            "commission_paid": round(self.commission_paid, 2),
            "slippage_paid": round(self.slippage_paid, 2),
            "total_cost": round(self.commission_paid + self.slippage_paid, 2),
            "cash_after": round(self.cash_after, 2),
        }


async def select_candidates(
    db: AsyncSession, top_n: int = DEFAULT_POSITIONS
) -> list[Candidate]:
    """The highest earnings surprises in the universe, best first.

    Ranked on the raw surprise rather than its percentile: the percentile is
    what the validation measured, and over a fixed universe on one day the two
    order identically. The raw number is reported because "beat by 18%" is a
    fact a reader can check, and "94th percentile" is one they cannot.
    """
    from app.services import fundamentals as fundamentals_service

    stocks = list(
        (
            await db.execute(select(Stock).where(Stock.is_active.is_(True)))
        ).scalars()
    )
    # The surprise is derived from stored earnings rows rather than held on a
    # column, so it comes from the same loader the score uses. Reimplementing
    # the arithmetic here would let the strategy and the validation that
    # justified it drift apart.
    factors = await fundamentals_service.load_all(db)

    ranked = sorted(
        (
            (stock, factors[stock.id].earnings_surprise_pct)
            for stock in stocks
            if stock.id in factors
            and factors[stock.id].earnings_surprise_pct is not None
        ),
        key=lambda pair: pair[1],
        reverse=True,
    )[:top_n]

    if not ranked:
        return []

    prices = await latest_prices(db, [stock.ticker for stock, _ in ranked])
    return [
        Candidate(
            ticker=stock.ticker,
            company_name=stock.company_name,
            surprise_pct=surprise,
            price=prices.get(stock.ticker),
        )
        for stock, surprise in ranked
    ]


def _fill_price(price: float, side: TradeSide, slippage_bps: float) -> float:
    """The price actually paid, after crossing the spread.

    A buy lifts the offer and a sell hits the bid, so both are worse than the
    mid the platform stores. Modelling fills at the stored price is the single
    most flattering assumption a paper record can make.
    """
    edge = price * (slippage_bps / 10_000.0)
    return price + edge if side is TradeSide.BUY else price - edge


async def rebalance(
    db: AsyncSession,
    portfolio_id: int,
    top_n: int = DEFAULT_POSITIONS,
    commission: float = 1.0,
    slippage_bps: float = 5.0,
) -> RebalanceReport:
    """Move a paper portfolio to the current top-N earnings surprises.

    Equal-weight by total account value rather than by cash: sizing off cash
    alone would shrink every position as the portfolio became invested, so the
    first name bought would carry several times the weight of the last.
    """
    report = RebalanceReport()

    portfolio = (
        await db.execute(select(Portfolio).where(Portfolio.id == portfolio_id))
    ).scalar_one_or_none()
    if portfolio is None:
        raise LookupError(f"No portfolio {portfolio_id}")

    candidates = await select_candidates(db, top_n)
    report.selected = [c.ticker for c in candidates]
    target = {c.ticker: c for c in candidates}

    positions = await get_positions(db, portfolio_id)
    open_now = {t: p for t, p in positions.items() if p.quantity > 1e-9}

    # --- close what dropped out -------------------------------------------
    prices_held = await latest_prices(db, list(open_now))
    for ticker, position in open_now.items():
        if ticker in target:
            report.held.append(ticker)
            continue
        price = prices_held.get(ticker)
        if price is None:
            report.skipped.append({"ticker": ticker, "reason": "no price to sell at"})
            continue
        stock = (
            await db.execute(select(Stock).where(Stock.ticker == ticker))
        ).scalar_one()
        fill = _fill_price(price, TradeSide.SELL, slippage_bps)
        await execute_trade(
            db, portfolio, stock, TradeSide.SELL, position.quantity, fill,
            rationale=f"{SETUP_NAME}: dropped out of top {top_n}",
            setup=SETUP_NAME,
        )
        portfolio.cash -= commission
        report.commission_paid += commission
        report.slippage_paid += abs(price - fill) * position.quantity
        report.closed.append(
            {"ticker": ticker, "quantity": position.quantity, "price": round(fill, 4)}
        )

    # --- open what came in -------------------------------------------------
    # Sized off total value so weights stay even as cash is consumed.
    positions_value = sum(
        p.quantity * (prices_held.get(t) or p.average_cost)
        for t, p in open_now.items()
        if t in target
    )
    total_value = portfolio.cash + positions_value
    budget_each = total_value / top_n if top_n else 0.0

    for candidate in candidates:
        if candidate.ticker in open_now:
            continue
        if candidate.price is None:
            report.skipped.append(
                {"ticker": candidate.ticker, "reason": "no stored price"}
            )
            continue

        fill = _fill_price(candidate.price, TradeSide.BUY, slippage_bps)
        shares = int((budget_each - commission) // fill)
        if shares < 1:
            report.skipped.append(
                {
                    "ticker": candidate.ticker,
                    "reason": f"one share costs {fill:.2f}, budget per name is "
                    f"{budget_each:.2f}",
                }
            )
            continue
        cost = shares * fill + commission
        if cost > portfolio.cash:
            report.skipped.append(
                {"ticker": candidate.ticker, "reason": "not enough cash left"}
            )
            continue

        stock = (
            await db.execute(select(Stock).where(Stock.ticker == candidate.ticker))
        ).scalar_one()
        await execute_trade(
            db, portfolio, stock, TradeSide.BUY, shares, fill,
            rationale=(
                f"{SETUP_NAME}: surprise {candidate.surprise_pct:+.1f}%"
            ),
            setup=SETUP_NAME,
        )
        portfolio.cash -= commission
        report.commission_paid += commission
        report.slippage_paid += abs(fill - candidate.price) * shares
        report.opened.append(
            {
                "ticker": candidate.ticker,
                "shares": shares,
                "price": round(fill, 4),
                "surprise_pct": round(candidate.surprise_pct, 2),
            }
        )

    report.cash_after = portfolio.cash
    await db.commit()
    logger.info("Earnings drift rebalance: %s", report.as_dict())
    return report
