"""Shared price representation and the upsert every price source writes through.

Both vendors deliver the same shape — one day's OHLCV for one symbol — so the
storage path belongs here rather than inside either integration. Keeping one
upsert also keeps the "one row per (ticker, trading day)" invariant in a single
place: repeated intraday refreshes update that row instead of multiplying it.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Stock, StockPrice


@dataclass(frozen=True)
class Quote:
    """One day's OHLCV for one symbol."""

    symbol: str
    open: float | None
    high: float | None
    low: float | None
    close: float
    volume: int | None
    trading_day: datetime


async def upsert_quotes(
    db: AsyncSession, stock: Stock, quotes: list[Quote], source: str
) -> dict[str, int]:
    """Insert new (ticker, day) rows, update rows the ingest has seen before.

    A plain SELECT-then-write is deliberate: batch sizes here are tiny and it
    stays portable across PostgreSQL and the SQLite test database, where
    ON CONFLICT syntax differs.
    """
    if not quotes:
        return {"inserted": 0, "updated": 0}

    days = [quote.trading_day for quote in quotes]
    existing = {
        price.price_date.replace(tzinfo=timezone.utc): price
        for price in (
            await db.execute(
                select(StockPrice).where(
                    StockPrice.ticker_id == stock.id, StockPrice.price_date.in_(days)
                )
            )
        ).scalars()
    }

    inserted = updated = 0
    for quote in quotes:
        row = existing.get(quote.trading_day)
        if row is None:
            db.add(
                StockPrice(
                    ticker_id=stock.id,
                    open=quote.open,
                    high=quote.high,
                    low=quote.low,
                    close=quote.close,
                    volume=quote.volume,
                    price_date=quote.trading_day,
                    source=source,
                )
            )
            inserted += 1
        else:
            row.open, row.high, row.low = quote.open, quote.high, quote.low
            row.close, row.volume, row.source = quote.close, quote.volume, source
            updated += 1

    await db.commit()
    return {"inserted": inserted, "updated": updated}
