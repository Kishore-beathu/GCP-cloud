"""Closing paper positions when their stop or target trades.

The paper portfolio marked positions to market but never exited them. A stop
lived as prose inside ``rationale``, which nothing could read, so a position
whose stop was breached at 15:40 while nobody was watching simply stayed open —
and the log then recorded a loss the plan had said to cut at a known amount.

That is not only a worse result, it is a *biased record*. The whole reason for
keeping a paper log is to find out whether these setups pay. A log where the
winners were closed at target and the losers were left running until someone
noticed measures attentiveness, not the setup.

Three decisions worth stating:

**It fills at the observed price, not at the level.** If a bar trades through
the stop, filling at the stop would credit a fill that was not available. The
monitor records what the bar actually shows and notes the planned level beside
it, so slippage stays visible instead of being quietly absorbed.

**It refuses to act on a stale bar.** An exit priced off a twenty-minute-old
bar is fiction. Delayed venues are skipped and named rather than exited at a
price that was never current.

**It closes the whole position.** Partial exits and trailing stops are real
techniques, but each needs its own rules, and inventing them here would put
decisions in the log that nobody chose.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.integrations.yahoo import YahooUnavailable, fetch_intraday
from app.models import Portfolio, Stock, Trade, TradeSide
from app.services.portfolio import execute_trade, get_positions

logger = logging.getLogger(__name__)

# The window whose last bar prices the exit. Five-minute bars, matching the
# setups that produced the entries.
PRICE_WINDOW = "1d"

# An exit priced off a bar older than this is not an exit, it is a guess. Non-US
# venues are delayed 15-20 minutes on this feed and will always fail it, which
# is the correct outcome rather than a bug.
MAX_BAR_AGE_MINUTES = 10.0

REQUEST_DELAY_SECONDS = 0.15


@dataclass
class ExitReport:
    """What one exit sweep did."""

    positions_checked: int = 0
    exits: list[dict] = field(default_factory=list)
    # Positions left open because no stop or target was recorded on the entry.
    unplanned: list[str] = field(default_factory=list)
    # Priced off a bar too old to act on, named rather than silently skipped.
    stale: list[str] = field(default_factory=list)
    failures: dict[str, int] = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {
            "positions_checked": self.positions_checked,
            "exits": self.exits,
            "exits_taken": len(self.exits),
            "unplanned": self.unplanned,
            "stale": self.stale,
            "failures": self.failures,
        }


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


async def _opening_plan(
    db: AsyncSession, portfolio_id: int, ticker: str
) -> Trade | None:
    """The most recent buy for this symbol that carried a stop or a target.

    The most recent, not the first: a position added to twice is governed by
    the latest plan its owner set, and an older plan is a level they have
    already moved on from.
    """
    return (
        await db.execute(
            select(Trade)
            .join(Stock, Stock.id == Trade.ticker_id)
            .where(
                Trade.portfolio_id == portfolio_id,
                Stock.ticker == ticker,
                Trade.side == TradeSide.BUY.value,
            )
            .order_by(Trade.executed_at.desc(), Trade.id.desc())
            .limit(1)
        )
    ).scalar_one_or_none()


def _breach(price: float, stop: float | None, target: float | None) -> str | None:
    """Which level a long position has traded through, if either.

    The stop is checked first. A bar whose range covers both levels is a bar
    the position could have been stopped out of before the target printed, and
    assuming the happier of the two is how a paper record flatters itself.
    """
    if stop is not None and price <= stop:
        return "stop"
    if target is not None and price >= target:
        return "target"
    return None


async def check_exits(
    db: AsyncSession, portfolio_id: int | None = None
) -> ExitReport:
    """Close any paper position whose stop or target has traded."""
    report = ExitReport()

    query = select(Portfolio)
    if portfolio_id is not None:
        query = query.where(Portfolio.id == portfolio_id)
    portfolios = list((await db.execute(query)).scalars())

    for portfolio in portfolios:
        positions = await get_positions(db, portfolio.id)
        open_positions = [p for p in positions.values() if p.quantity > 1e-9]

        for index, position in enumerate(open_positions):
            report.positions_checked += 1
            if index:
                await asyncio.sleep(REQUEST_DELAY_SECONDS)

            plan = await _opening_plan(db, portfolio.id, position.ticker)
            if plan is None or (plan.stop is None and plan.target is None):
                report.unplanned.append(position.ticker)
                continue

            try:
                bars = await fetch_intraday(position.ticker, PRICE_WINDOW)
            except YahooUnavailable as exc:
                logger.warning("Exit check stopped at %s: %s", position.ticker, exc)
                report.failures["YahooUnavailable"] = (
                    report.failures.get("YahooUnavailable", 0) + 1
                )
                break
            except Exception as exc:  # noqa: BLE001 - one symbol is not the sweep
                logger.exception("Exit check failed for %s", position.ticker)
                report.failures[type(exc).__name__] = (
                    report.failures.get(type(exc).__name__, 0) + 1
                )
                continue

            if not bars:
                report.stale.append(position.ticker)
                continue

            last = bars[-1]
            age = (
                datetime.now(timezone.utc) - _aware(last.at)
            ).total_seconds() / 60.0
            if age > MAX_BAR_AGE_MINUTES:
                report.stale.append(position.ticker)
                continue

            level = _breach(last.close, plan.stop, plan.target)
            if level is None:
                continue

            planned = plan.stop if level == "stop" else plan.target
            stock = (
                await db.execute(
                    select(Stock).where(Stock.ticker == position.ticker)
                )
            ).scalar_one()

            # Filled at the observed price, with the planned level recorded
            # beside it. A stop that gapped is a worse fill than the plan, and
            # hiding that would make every future hit-rate number optimistic.
            await execute_trade(
                db,
                portfolio,
                stock,
                TradeSide.SELL,
                position.quantity,
                last.close,
                rationale=f"{level} hit at {last.close:.2f} (planned {planned:.2f})",
                setup=plan.setup,
            )
            report.exits.append(
                {
                    "portfolio_id": portfolio.id,
                    "ticker": position.ticker,
                    "level": level,
                    "planned": round(planned, 4),
                    "filled": round(last.close, 4),
                    "slippage": round(
                        last.close - planned if level == "stop" else planned - last.close,
                        4,
                    ),
                    "quantity": position.quantity,
                    "entry": round(position.average_cost, 4),
                    "pnl": round(
                        (last.close - position.average_cost) * position.quantity, 2
                    ),
                    "setup": plan.setup,
                }
            )

    if report.exits:
        await db.commit()
        logger.info("Paper exits: %s", report.as_dict())
    return report
