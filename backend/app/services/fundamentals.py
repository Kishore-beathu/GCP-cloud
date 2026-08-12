"""Market cap, earnings surprise, and which way analyst opinion is moving.

Three things the platform could not see, added because the evidence behind
them is stronger than the evidence behind anything already here:

* **Earnings surprise.** Post-earnings-announcement drift is among the most
  replicated effects in the published literature. The news pipeline could see
  that a company had *reported*; it had no idea whether the number beat.
* **Analyst opinion movement.** True estimate revisions need a paid feed. The
  free recommendation trend is a stand-in: the counts say little, but the
  month-on-month change says which way opinion is moving.
* **Market cap.** A column since the first migration with nothing ever writing
  to it, so every row read NULL while the API served the field. Without it a
  small/mid-cap universe cannot be expressed at all.

**None of these is weighted in the live score yet.** They are computed,
exposed and measured — `scoring.validate()` reports a `fundamental` ranking
alongside the others — and they earn a weight only if that measurement
supports one. The sentiment pillar is why: it was given 40% on the reasonable
assumption that news matters, and twelve periods later the honest reading was
that it had not been shown to.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.integrations.finnhub import (
    FinnhubRateLimited,
    FinnhubRejected,
    fetch_earnings,
    fetch_profile,
    fetch_recommendations,
)
from app.models import AnalystTrend, EarningsReport, Stock

logger = logging.getLogger(__name__)

# Finnhub's free tier covers US listings. Everything else returns an empty
# profile, which is reported as uncovered rather than retried.
REQUEST_DELAY_SECONDS = 0.35

# A market cap older than this is refreshed. Share counts move on buybacks and
# issuance, not by the minute.
STALE_FUNDAMENTALS_DAYS = 7


@dataclass
class FundamentalsReport:
    """What one ingest pass did."""

    symbols: int = 0
    profiles_updated: int = 0
    earnings_stored: int = 0
    trends_stored: int = 0
    uncovered: list[str] = field(default_factory=list)
    note: str | None = None

    def as_dict(self) -> dict:
        return {
            "symbols": self.symbols,
            "profiles_updated": self.profiles_updated,
            "earnings_stored": self.earnings_stored,
            "trends_stored": self.trends_stored,
            "uncovered": self.uncovered,
            "note": self.note,
        }


async def ingest_fundamentals(
    db: AsyncSession,
    tickers: list[str] | None = None,
    only_stale: bool = True,
) -> FundamentalsReport:
    """Refresh profile, earnings and analyst trend for the given symbols.

    Three calls per symbol, so this is the most request-hungry job here. It
    defaults to only the symbols whose fundamentals are missing or a week old,
    because a share count does not change often enough to justify re-fetching
    the universe daily.
    """
    settings = get_settings()
    report = FundamentalsReport()

    if not settings.finnhub_api_key:
        report.note = "FINNHUB_API_KEY is not set; fundamentals need it."
        logger.info("Fundamentals ingest skipped: no Finnhub key")
        return report

    stocks = await _stocks_to_refresh(db, tickers, only_stale)
    report.symbols = len(stocks)
    if not stocks:
        return report

    async with httpx.AsyncClient() as client:
        for index, stock in enumerate(stocks):
            try:
                covered = await _refresh_one(db, client, stock, settings.finnhub_api_key, report)
            except FinnhubRejected as exc:
                # About the account, not the symbol: every later call fails too.
                report.note = f"Finnhub refused the request: {exc}"
                logger.error("Fundamentals ingest stopped: %s", exc)
                break
            except FinnhubRateLimited:
                report.note = "Finnhub rate limit reached; run again later."
                logger.warning("Fundamentals ingest hit the rate limit at %s", stock.ticker)
                break

            if not covered:
                report.uncovered.append(stock.ticker)
            if index < len(stocks) - 1:
                import asyncio

                await asyncio.sleep(REQUEST_DELAY_SECONDS)

    await db.commit()
    logger.info("Fundamentals ingest complete: %s", report.as_dict())
    return report


async def _refresh_one(
    db: AsyncSession,
    client: httpx.AsyncClient,
    stock: Stock,
    api_key: str,
    report: FundamentalsReport,
) -> bool:
    """Update one symbol. Returns whether the vendor covered it at all."""
    profile = await fetch_profile(client, stock.ticker, api_key)
    covered = bool(profile)

    if profile:
        stock.market_cap = profile.get("market_cap")
        stock.shares_outstanding = profile.get("shares_outstanding")
        stock.fundamentals_at = datetime.now(timezone.utc)
        report.profiles_updated += 1

    for row in await fetch_earnings(client, stock.ticker, api_key):
        covered = True
        if await _upsert_earnings(db, stock.id, row):
            report.earnings_stored += 1

    for row in await fetch_recommendations(client, stock.ticker, api_key):
        covered = True
        if await _upsert_trend(db, stock.id, row):
            report.trends_stored += 1

    return covered


async def _upsert_earnings(db: AsyncSession, ticker_id: int, row: dict) -> bool:
    """Store a quarter, or update it if the vendor has revised the figures."""
    existing = (
        await db.execute(
            select(EarningsReport).where(
                EarningsReport.ticker_id == ticker_id,
                EarningsReport.period == row["period"],
            )
        )
    ).scalar_one_or_none()

    if existing:
        changed = (
            existing.eps_actual != row["eps_actual"]
            or existing.eps_estimate != row["eps_estimate"]
        )
        if changed:
            existing.eps_actual = row["eps_actual"]
            existing.eps_estimate = row["eps_estimate"]
            existing.eps_surprise_pct = row["eps_surprise_pct"]
        return changed

    db.add(
        EarningsReport(
            ticker_id=ticker_id,
            period=row["period"],
            eps_actual=row["eps_actual"],
            eps_estimate=row["eps_estimate"],
            eps_surprise_pct=row["eps_surprise_pct"],
        )
    )
    return True


async def _upsert_trend(db: AsyncSession, ticker_id: int, row: dict) -> bool:
    existing = (
        await db.execute(
            select(AnalystTrend).where(
                AnalystTrend.ticker_id == ticker_id,
                AnalystTrend.period == row["period"],
            )
        )
    ).scalar_one_or_none()

    if existing:
        for key in ("strong_buy", "buy", "hold", "sell", "strong_sell"):
            setattr(existing, key, row[key])
        return False

    db.add(AnalystTrend(ticker_id=ticker_id, **row))
    return True


async def _stocks_to_refresh(
    db: AsyncSession, tickers: list[str] | None, only_stale: bool
) -> list[Stock]:
    query = select(Stock).where(Stock.is_active.is_(True)).order_by(Stock.ticker)
    if tickers:
        query = query.where(Stock.ticker.in_([t.upper() for t in tickers]))
    stocks = list((await db.execute(query)).scalars())

    if not only_stale:
        return stocks

    cutoff = datetime.now(timezone.utc) - timedelta(days=STALE_FUNDAMENTALS_DAYS)
    return [
        stock
        for stock in stocks
        if stock.fundamentals_at is None
        or _aware(stock.fundamentals_at) < cutoff
    ]


def _aware(moment: datetime) -> datetime:
    return moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)


# --- Factors -----------------------------------------------------------------


@dataclass(frozen=True)
class Fundamentals:
    """The measurable inputs, per symbol, ready to be ranked."""

    market_cap: float | None = None
    # Surprise on the most recently reported quarter, in percent.
    earnings_surprise_pct: float | None = None
    # Days since that report. Drift is a decaying effect, so a beat six months
    # ago is not the same information as one from last week, and the ranking
    # needs to be able to tell them apart.
    days_since_earnings: int | None = None
    # Net change in analyst opinion between the two most recent months, as a
    # share of the analysts covering it. Positive means upgrades outweighed
    # downgrades.
    analyst_revision: float | None = None
    analysts_covering: int = 0

    def as_dict(self) -> dict:
        return {
            "market_cap": self.market_cap,
            "earnings_surprise_pct": self.earnings_surprise_pct,
            "days_since_earnings": self.days_since_earnings,
            "analyst_revision": self.analyst_revision,
            "analysts_covering": self.analysts_covering,
        }


def _opinion_score(trend: AnalystTrend) -> float | None:
    """One month's recommendation mix as a single number, -1 to 1.

    Weighted so a strong buy counts double a buy. Returns None when nobody
    covers the symbol, rather than 0 — "no analysts" and "analysts are evenly
    split" are different facts and averaging them together would let an
    uncovered small cap sit in the middle of the ranking on no information.
    """
    total = trend.strong_buy + trend.buy + trend.hold + trend.sell + trend.strong_sell
    if not total:
        return None
    weighted = (
        2 * trend.strong_buy + trend.buy - trend.sell - 2 * trend.strong_sell
    )
    return weighted / (2 * total)


def summarise(
    stock: Stock,
    earnings: list[EarningsReport],
    trends: list[AnalystTrend],
    now: datetime | None = None,
) -> Fundamentals:
    """Reduce one symbol's stored rows to the factors the ranking uses."""
    now = now or datetime.now(timezone.utc)

    surprise = None
    days_since = None
    reported = sorted(
        (row for row in earnings if row.eps_surprise_pct is not None),
        key=lambda row: row.period,
    )
    if reported:
        latest = reported[-1]
        surprise = latest.eps_surprise_pct
        days_since = max(0, (now - _aware(latest.period)).days)

    revision = None
    covering = 0
    ordered = sorted(trends, key=lambda row: row.period)
    if ordered:
        covering = (
            ordered[-1].strong_buy
            + ordered[-1].buy
            + ordered[-1].hold
            + ordered[-1].sell
            + ordered[-1].strong_sell
        )
    if len(ordered) >= 2:
        latest, previous = _opinion_score(ordered[-1]), _opinion_score(ordered[-2])
        if latest is not None and previous is not None:
            revision = round(latest - previous, 4)

    return Fundamentals(
        market_cap=stock.market_cap,
        earnings_surprise_pct=surprise,
        days_since_earnings=days_since,
        analyst_revision=revision,
        analysts_covering=covering,
    )


async def load_all(db: AsyncSession) -> dict[int, Fundamentals]:
    """Every active symbol's factors, in three queries rather than 3N."""
    stocks = list(
        (
            await db.execute(select(Stock).where(Stock.is_active.is_(True)))
        ).scalars()
    )
    if not stocks:
        return {}

    by_id = {stock.id: stock for stock in stocks}
    earnings: dict[int, list[EarningsReport]] = {}
    for row in (
        await db.execute(
            select(EarningsReport).where(EarningsReport.ticker_id.in_(by_id))
        )
    ).scalars():
        earnings.setdefault(row.ticker_id, []).append(row)

    trends: dict[int, list[AnalystTrend]] = {}
    for row in (
        await db.execute(select(AnalystTrend).where(AnalystTrend.ticker_id.in_(by_id)))
    ).scalars():
        trends.setdefault(row.ticker_id, []).append(row)

    return {
        stock.id: summarise(stock, earnings.get(stock.id, []), trends.get(stock.id, []))
        for stock in stocks
    }
