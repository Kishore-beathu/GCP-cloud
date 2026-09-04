"""Recording intraday bars, so the setups can eventually be measured.

`app/services/setups.py` says plainly that its four setups have an unknown hit
rate and cannot be validated, because `stock_prices` holds one row per trading
day and intraday bars were fetched live and thrown away. This module is the
missing half: it keeps them.

Three decisions worth stating, because each is a trade-off rather than an
obvious best answer.

**It re-fetches the whole session, not the newest bar.** One call returns the
day so far, and storing it is idempotent on ``(ticker, interval, at)``. That
costs nothing extra — the vendor returns the session either way — and it means
a run that was missed, or a process that was down for an hour, heals itself on
the next pass instead of leaving a hole that nothing will ever fill.

**It records a bounded set of symbols.** One request per symbol per run, and
the universe is over three hundred. Recording all of them every five minutes
is roughly one request a second sustained at a vendor with no contract, which
ends in a rate limit and no data at all. The caller names what to record.

**It stores volume as null when the vendor omits it.** A padded bar with no
volume is unknown, not zero, and zero would drag every relative-volume average
down with it.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.integrations.yahoo import Bar, YahooUnavailable, fetch_intraday
from app.models import IntradayBar, Stock

logger = logging.getLogger(__name__)

# The window whose bars we keep. "1d" returns the current session at a
# five-minute interval — long enough that a missed run is recoverable, fine
# enough for the setups, which are all defined on five-minute bars.
RECORD_WINDOW = "1d"
RECORD_INTERVAL = "5m"

# Between symbols. Yahoo has no published limit and no contract; this is the
# same pace every other vendor path here uses, and it keeps a 50-symbol run
# under ten seconds.
REQUEST_DELAY_SECONDS = 0.15

# How long recorded bars are kept. Ninety days of five-minute bars is a few
# hundred sessions' worth of setup triggers — enough to measure a hit rate —
# and at roughly 78 bars per symbol per day it stays small enough that the
# table does not become the largest thing in the database.
RETENTION_DAYS = 90


@dataclass
class IntradayRecordReport:
    """What one recording pass did."""

    symbols: int = 0
    bars_seen: int = 0
    bars_stored: int = 0
    # Bars the vendor returned that were already recorded. Expected and large:
    # each run re-reads the session, so only the newest bars are ever new.
    bars_already_known: int = 0
    # Symbols the vendor had nothing for, named rather than counted. A count
    # cannot distinguish a delisted symbol from an outage.
    uncovered: list[str] = field(default_factory=list)
    # Why a symbol could not be read, by exception name.
    failures: dict[str, int] = field(default_factory=dict)
    purged: int = 0

    def as_dict(self) -> dict:
        return {
            "symbols": self.symbols,
            "interval": RECORD_INTERVAL,
            "bars_seen": self.bars_seen,
            "bars_stored": self.bars_stored,
            "bars_already_known": self.bars_already_known,
            "uncovered": self.uncovered,
            "failures": self.failures,
            "purged": self.purged,
        }


async def _known_times(
    db: AsyncSession, ticker_id: int, interval: str, bars: list[Bar]
) -> set[datetime]:
    """The bar timestamps already stored for this symbol, among these bars.

    Read once per symbol rather than probed once per bar: a session is around
    eighty bars, and eighty round trips per symbol per run is what turns a
    ten-second pass into a ten-minute one.
    """
    if not bars:
        return set()
    rows = (
        await db.execute(
            select(IntradayBar.at).where(
                IntradayBar.ticker_id == ticker_id,
                IntradayBar.interval == interval,
                IntradayBar.at.in_([bar.at for bar in bars]),
            )
        )
    ).scalars()
    return {_aware(value) for value in rows}


def _aware(value: datetime) -> datetime:
    """SQLite hands back naive datetimes; compare them as UTC."""
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


async def record_intraday(
    db: AsyncSession,
    tickers: list[str] | None = None,
    group: str | None = None,
    limit: int = 50,
) -> IntradayRecordReport:
    """Fetch and store the current session's bars for the named symbols."""
    from app.services import sectors

    report = IntradayRecordReport()

    query = select(Stock).where(Stock.is_active.is_(True)).order_by(Stock.ticker)
    if tickers:
        query = query.where(Stock.ticker.in_([t.upper() for t in tickers]))
    if group:
        query = query.where(Stock.sector.in_(sectors.sectors_in(group)))
    stocks = list((await db.execute(query)).scalars())[:limit]

    report.symbols = len(stocks)
    if not stocks:
        return report

    for index, stock in enumerate(stocks):
        if index:
            await asyncio.sleep(REQUEST_DELAY_SECONDS)
        try:
            bars = await fetch_intraday(stock.ticker, RECORD_WINDOW)
        except YahooUnavailable as exc:
            # Refused or rate limited. That applies to every symbol after this
            # one too, so stop rather than spend the rest of the run being
            # told the same thing.
            report.failures["YahooUnavailable"] = (
                report.failures.get("YahooUnavailable", 0) + 1
            )
            logger.warning("Intraday recording stopped at %s: %s", stock.ticker, exc)
            break
        except Exception as exc:  # noqa: BLE001 - one bad symbol is not the run
            report.failures[type(exc).__name__] = (
                report.failures.get(type(exc).__name__, 0) + 1
            )
            logger.exception("Intraday recording failed for %s", stock.ticker)
            continue

        if not bars:
            report.uncovered.append(stock.ticker)
            continue

        report.bars_seen += len(bars)
        known = await _known_times(db, stock.id, RECORD_INTERVAL, bars)

        for bar in bars:
            if _aware(bar.at) in known:
                report.bars_already_known += 1
                continue
            db.add(
                IntradayBar(
                    ticker_id=stock.id,
                    interval=RECORD_INTERVAL,
                    at=bar.at,
                    open=bar.open,
                    high=bar.high,
                    low=bar.low,
                    close=bar.close,
                    volume=bar.volume,
                    source="yahoo",
                )
            )
            report.bars_stored += 1

    report.purged = await purge_old_bars(db)
    await db.commit()
    logger.info("Intraday recording: %s", report.as_dict())
    return report


async def purge_old_bars(db: AsyncSession, days: int = RETENTION_DAYS) -> int:
    """Drop bars older than the retention window. Returns rows removed."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    result = await db.execute(delete(IntradayBar).where(IntradayBar.at < cutoff))
    return result.rowcount or 0


async def stored_bars(
    db: AsyncSession,
    ticker_id: int,
    start: datetime,
    end: datetime,
    interval: str = RECORD_INTERVAL,
) -> list[Bar]:
    """Recorded bars for one symbol over a window, oldest first.

    Returns the same ``Bar`` shape the live fetch does, so a replay can hand
    stored history to the setup evaluators unchanged — which is the whole
    point of recording them.
    """
    rows = (
        await db.execute(
            select(IntradayBar)
            .where(
                IntradayBar.ticker_id == ticker_id,
                IntradayBar.interval == interval,
                IntradayBar.at >= start,
                IntradayBar.at <= end,
            )
            .order_by(IntradayBar.at)
        )
    ).scalars()
    return [
        Bar(
            at=_aware(row.at),
            close=row.close,
            open=row.open,
            high=row.high,
            low=row.low,
            volume=row.volume,
        )
        for row in rows
    ]


async def coverage(db: AsyncSession) -> dict:
    """How much history has accumulated, so progress toward measurable is visible."""
    from sqlalchemy import func

    total, symbols, first, last = (
        await db.execute(
            select(
                func.count(IntradayBar.id),
                func.count(func.distinct(IntradayBar.ticker_id)),
                func.min(IntradayBar.at),
                func.max(IntradayBar.at),
            )
        )
    ).one()

    sessions = 0
    if first is not None and last is not None:
        # Calendar days spanned, not trading days — an approximation, and
        # labelled as one rather than dressed up as a session count.
        sessions = (_aware(last).date() - _aware(first).date()).days + 1

    return {
        "bars": total or 0,
        "symbols": symbols or 0,
        "earliest": _aware(first).isoformat() if first else None,
        "latest": _aware(last).isoformat() if last else None,
        "calendar_days_spanned": sessions,
        "retention_days": RETENTION_DAYS,
    }
