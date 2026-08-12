"""Fundamentals, earnings surprise, and the forward catalyst calendar."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models import AnalystTrend, EarningsReport, Stock
from app.security import require_auth
from app.services import catalysts, fundamentals, sectors
from app.services.tickers import tickers_in_group

router = APIRouter(tags=["fundamentals"])


@router.get("/fundamentals", summary="Market cap, earnings surprise and analyst movement")
async def list_fundamentals(
    group: str | None = Query(default=None, description="Limit to an industry group"),
    min_market_cap: float | None = Query(
        default=None, ge=0, description="In units of the listing currency, not millions"
    ),
    max_market_cap: float | None = Query(default=None, ge=0),
    has_earnings: bool = Query(
        default=False, description="Only symbols with a stored earnings surprise"
    ),
    limit: int = Query(default=100, ge=1, le=1000),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """The factors, per symbol, with the coverage gaps visible.

    Market cap is US-only on the free vendor tier, so a European or Asian
    listing reports None. That is a coverage fact rather than a failure, and
    `covered` counts it so a small-cap filter cannot silently drop two thirds
    of the universe without saying so.
    """
    if group and not sectors.sectors_in(group.strip().lower()):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Unknown group {group!r}. Try GET /stocks/sectors.",
        )

    factors = await fundamentals.load_all(db)
    stocks = {
        stock.id: stock
        for stock in (
            await db.execute(select(Stock).where(Stock.is_active.is_(True)))
        ).scalars()
    }

    wanted: set[str] | None = None
    if group:
        wanted = {symbol.upper() for symbol in await tickers_in_group(db, group)}

    rows: list[dict] = []
    for ticker_id, factor in factors.items():
        stock = stocks.get(ticker_id)
        if stock is None:
            continue
        if wanted is not None and stock.ticker.upper() not in wanted:
            continue
        if has_earnings and factor.earnings_surprise_pct is None:
            continue
        if min_market_cap is not None and (
            factor.market_cap is None or factor.market_cap < min_market_cap
        ):
            continue
        if max_market_cap is not None and (
            factor.market_cap is None or factor.market_cap > max_market_cap
        ):
            continue

        rows.append(
            {
                "ticker": stock.ticker,
                "company_name": stock.company_name,
                "sector_group": sectors.group_for(stock.sector),
                **factor.as_dict(),
            }
        )

    rows.sort(
        key=lambda row: (
            row["earnings_surprise_pct"] is None,
            -(row["earnings_surprise_pct"] or 0),
        )
    )

    with_cap = sum(1 for row in factors.values() if row.market_cap is not None)
    return {
        "universe": len(factors),
        "matched": len(rows),
        "coverage": {
            "market_cap": with_cap,
            "earnings": sum(
                1 for row in factors.values() if row.earnings_surprise_pct is not None
            ),
            "analyst_revision": sum(
                1 for row in factors.values() if row.analyst_revision is not None
            ),
            "note": (
                "The free vendor tier covers US listings. Non-US symbols report "
                "None rather than a stale or invented value."
            ),
        },
        "rows": rows[:limit],
    }


@router.get("/fundamentals/{ticker}", summary="One symbol's stored fundamentals")
async def get_fundamentals(ticker: str, db: AsyncSession = Depends(get_db)) -> dict:
    symbol = ticker.strip().upper()
    stock = (
        await db.execute(select(Stock).where(Stock.ticker == symbol))
    ).scalar_one_or_none()
    if stock is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"{symbol} not tracked")

    earnings = list(
        (
            await db.execute(
                select(EarningsReport)
                .where(EarningsReport.ticker_id == stock.id)
                .order_by(EarningsReport.period.desc())
            )
        ).scalars()
    )
    trends = list(
        (
            await db.execute(
                select(AnalystTrend)
                .where(AnalystTrend.ticker_id == stock.id)
                .order_by(AnalystTrend.period.desc())
            )
        ).scalars()
    )

    return {
        "ticker": stock.ticker,
        "company_name": stock.company_name,
        "fundamentals_at": (
            stock.fundamentals_at.isoformat() if stock.fundamentals_at else None
        ),
        **fundamentals.summarise(stock, earnings, trends).as_dict(),
        "earnings_history": [
            {
                "period": row.period.isoformat(),
                "eps_actual": row.eps_actual,
                "eps_estimate": row.eps_estimate,
                "eps_surprise_pct": row.eps_surprise_pct,
            }
            for row in earnings[:8]
        ],
        "analyst_history": [
            {
                "period": row.period.isoformat(),
                "strong_buy": row.strong_buy,
                "buy": row.buy,
                "hold": row.hold,
                "sell": row.sell,
                "strong_sell": row.strong_sell,
            }
            for row in trends[:6]
        ],
    }


@router.get("/calendar", summary="What is scheduled to happen next")
async def get_calendar(
    days: int = Query(default=7, ge=1, le=60),
    kind: str | None = Query(default=None, description="earnings or trial_readout"),
    ticker: list[str] | None = Query(default=None),
    group: str | None = Query(default=None),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Scheduled, unresolved events for the tracked universe.

    The one forward-looking thing here. Everything else answers "what
    happened"; this answers "what is coming", which is the question a report
    delivered the evening before is actually for.
    """
    symbols = list(ticker or [])
    if group:
        if not sectors.sectors_in(group.strip().lower()):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"Unknown group {group!r}. Try GET /stocks/sectors.",
            )
        members = await tickers_in_group(db, group)
        symbols = [s for s in members if not ticker or s.upper() in {t.upper() for t in ticker}]

    events = await catalysts.upcoming(db, days=days, kind=kind, tickers=symbols or None)
    return {
        "days": days,
        "events": events,
        "by_kind": {
            name: sum(1 for event in events if event["kind"] == name)
            for name in sorted({event["kind"] for event in events})
        },
        "caveat": (
            "Earnings dates are company-confirmed. Trial completion dates are "
            "the sponsor's estimate and slip routinely — confidence says which "
            "is which. PDUFA dates are absent: there is no free structured "
            "feed for them, and parsing one out of a press release would put a "
            "confident date on a guess."
        ),
    }


@router.post(
    "/admin/ingest/fundamentals",
    summary="Refresh market cap, earnings and analyst trends",
    dependencies=[Depends(require_auth)],
)
async def trigger_fundamentals(
    ticker: list[str] | None = Query(default=None),
    only_stale: bool = Query(
        default=True, description="False re-fetches everything, not just what is missing"
    ),
    include_non_us: bool = Query(
        default=False,
        description=(
            "Ask for non-US listings too. The free tier does not cover them, so "
            "this spends requests on calls that return nothing."
        ),
    ),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Three vendor calls per symbol, so it runs inline and reports counts."""
    report = await fundamentals.ingest_fundamentals(db, ticker, only_stale, include_non_us)
    return report.as_dict()


@router.post(
    "/admin/ingest/calendar",
    summary="Rebuild the forward catalyst calendar",
    dependencies=[Depends(require_auth)],
)
async def trigger_calendar(
    horizon_days: int = Query(default=catalysts.HORIZON_DAYS, ge=1, le=60),
    db: AsyncSession = Depends(get_db),
) -> dict:
    report = await catalysts.refresh_calendar(db, horizon_days)
    return report.as_dict()
