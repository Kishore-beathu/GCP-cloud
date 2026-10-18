"""Suspected splits and other unadjusted corporate actions.

Not a capability gap — a correctness bug with a long fuse. An unadjusted 2-for-1
split halves the close overnight, and every price factor reads that as a 50%
crash: momentum collapses, realised volatility spikes, the 52-week range
position drops to its floor. The backtester reads it as a catastrophic period.
And because pillar weights are set from what the backtester reports, one split
inside a validation window quietly moves the weights of the whole score.

**Detection before adjustment, deliberately.** Adjusting requires knowing the
ratio, and inferring a ratio from the price move is how a genuine crash gets
"corrected" into a split that never happened — destroying real data to fix
imagined data. What this does is find the discontinuities and name them, so
they can be excluded from factor computation and checked by a human.

A large single-day move is not proof. A biotech can genuinely halve on a failed
readout, and this universe is full of biotechs. So the test is deliberately two
things at once: the move has to be large *and* close to a simple whole-number
ratio, because a 2:1, 3:1 or 3:2 split lands on a suspiciously round number and
a trial failure does not.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Stock, StockPrice

logger = logging.getLogger(__name__)

# Used only for the "large move that matched nothing" count — a rough line
# above which a fall is worth noticing as a real event. It is deliberately not
# the split test: the ratio table below already implies a minimum move, since
# the smallest split in it (3:2) is a 33% one, and gating on size as well would
# have silently excluded exactly that case.
MIN_MOVE = 0.40

# Ratios worth recognising, as the factor the price is multiplied by. Forward
# splits divide the price; reverse splits multiply it.
KNOWN_RATIOS: tuple[tuple[str, float], ...] = (
    ("2:1", 0.5),
    ("3:1", 1 / 3),
    ("4:1", 0.25),
    ("5:1", 0.2),
    ("10:1", 0.1),
    ("3:2", 2 / 3),
    ("1:2 reverse", 2.0),
    ("1:5 reverse", 5.0),
    ("1:10 reverse", 10.0),
    ("1:20 reverse", 20.0),
)

# How close the observed move has to sit to a known ratio. Five percent is
# loose enough to survive a day's ordinary drift on top of the split and tight
# enough that an arbitrary crash does not land on one by chance.
RATIO_TOLERANCE = 0.05


@dataclass
class SuspectedAction:
    """One price discontinuity that looks like a corporate action."""

    ticker: str
    occurred_on: date
    previous_close: float
    close: float
    ratio: float
    matched: str | None

    def as_dict(self) -> dict:
        return {
            "ticker": self.ticker,
            "occurred_on": self.occurred_on.isoformat(),
            "previous_close": round(self.previous_close, 4),
            "close": round(self.close, 4),
            "ratio": round(self.ratio, 4),
            # None means the move is large but does not sit near a common
            # split ratio, which is what a real crash looks like.
            "matched_ratio": self.matched,
        }


@dataclass
class ActionReport:
    symbols_examined: int = 0
    prices_examined: int = 0
    suspected: list[dict] = field(default_factory=list)
    unmatched_moves: int = 0

    def as_dict(self) -> dict:
        return {
            "symbols_examined": self.symbols_examined,
            "prices_examined": self.prices_examined,
            "suspected_splits": len(self.suspected),
            # Large moves that match no known ratio: probably real, and worth
            # separating so the count of suspected splits is not inflated by
            # every bad day in the universe.
            "unmatched_large_moves": self.unmatched_moves,
            "suspected": self.suspected,
            "caveat": (
                "Detection only — nothing is adjusted. A ratio is inferred from "
                "the price move, and inferring one from a genuine crash would "
                "destroy real data to fix imagined data. Confirm against the "
                "issuer before acting."
            ),
        }


def match_ratio(previous_close: float, close: float) -> tuple[float, str | None]:
    """The observed price factor, and the split ratio it resembles."""
    ratio = close / previous_close
    for name, expected in KNOWN_RATIOS:
        if abs(ratio - expected) <= RATIO_TOLERANCE * expected:
            return ratio, name
    return ratio, None


def is_suspicious(previous_close: float, close: float) -> bool:
    """Does this move land on a common split ratio?

    A biotech halving on a failed readout is a 50% move that means exactly what
    it says. A 2-for-1 split is a 50% move that means nothing at all. Size
    cannot separate them; roundness can, and roundness is the whole test.

    The loosest case is 3:2, a 33% move, which is close enough to an ordinary
    bad day in this universe that it will occasionally fire on a real one. That
    is the right way round for a detector whose output is a list to confirm
    rather than an adjustment to apply.
    """
    if previous_close <= 0 or close <= 0:
        return False
    _, matched = match_ratio(previous_close, close)
    return matched is not None


async def detect(
    db: AsyncSession, days: int = 400, tickers: list[str] | None = None
) -> ActionReport:
    """Scan stored price history for unadjusted corporate actions."""
    report = ActionReport()
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)

    query = select(Stock).where(Stock.is_active.is_(True))
    if tickers:
        query = query.where(Stock.ticker.in_([t.upper() for t in tickers]))
    stocks = {stock.id: stock for stock in (await db.execute(query)).scalars()}
    if not stocks:
        return report
    report.symbols_examined = len(stocks)

    rows = (
        await db.execute(
            select(StockPrice)
            .where(StockPrice.ticker_id.in_(stocks), StockPrice.price_date >= cutoff)
            .order_by(StockPrice.ticker_id, StockPrice.price_date)
        )
    ).scalars()

    series: dict[int, list[StockPrice]] = {}
    for row in rows:
        series.setdefault(row.ticker_id, []).append(row)

    for ticker_id, prices in series.items():
        report.prices_examined += len(prices)
        for previous, current in zip(prices, prices[1:]):
            if previous.close <= 0 or current.close <= 0:
                continue
            ratio, matched = match_ratio(previous.close, current.close)
            if matched is None:
                if abs(1 - ratio) >= MIN_MOVE:
                    report.unmatched_moves += 1
                continue
            report.suspected.append(
                SuspectedAction(
                    ticker=stocks[ticker_id].ticker,
                    occurred_on=current.price_date.date(),
                    previous_close=previous.close,
                    close=current.close,
                    ratio=ratio,
                    matched=matched,
                ).as_dict()
            )

    logger.info(
        "Corporate action scan: %d suspected, %d unmatched large moves",
        len(report.suspected),
        report.unmatched_moves,
    )
    return report
