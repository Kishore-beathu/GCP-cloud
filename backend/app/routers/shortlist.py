"""The shortlist: positive news and a live setup, in one call."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models import Stock
from app.routers.setups import ACTIONABLE_BAR_AGE_MINUTES, MAX_SYMBOLS, run_scan
from app.services import sectors, setups, shortlist
from app.services.tickers import tickers_in_group

router = APIRouter(tags=["shortlist"])


@router.get("/shortlist", summary="Symbols with positive news and a live setup")
async def get_shortlist(
    group: str | None = Query(
        default="pharma_life_sciences", description="Industry group to scan"
    ),
    sector: str | None = Query(
        default=None, description="Narrow to one sector, e.g. clinical_stage"
    ),
    hours: int = Query(
        default=shortlist.DEFAULT_HOURS, ge=1, le=168, description="News lookback"
    ),
    min_score: float = Query(default=shortlist.DEFAULT_MIN_SCORE, ge=-1.0, le=1.0),
    direction: str = Query(default="long", pattern="^(long|short|any)$"),
    account_equity: float | None = Query(
        default=None, gt=0, description="If given, each row carries a position size"
    ),
    risk_fraction: float = Query(default=setups.DEFAULT_RISK_FRACTION, gt=0, le=0.05),
    include_delayed: bool = Query(
        default=False, description="Include venues this feed delays ~20 minutes"
    ),
    max_bar_age_minutes: float = Query(default=ACTIONABLE_BAR_AGE_MINUTES, gt=0),
    tz: str = Query(default="UTC"),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Which symbols have a positive catalyst *and* somewhere to put a stop.

    Three sources answering one question. Read separately they invite the two
    mistakes this is meant to prevent: acting on a headline with no entry or
    stop defined, and taking a setup without noticing the company reports
    tomorrow morning.

    **This does not forecast.** A row means a positive story landed inside the
    lookback and a setup is live with a defined stop — a reason to look, not a
    reading of what the price will do. The intraday setups have no measured hit
    rate on this universe (bars are fetched live and never stored, so they
    cannot be backtested), which is why rows are ordered by the news score,
    the one quantity here that has been measured.
    """
    if group and not sectors.sectors_in(group.strip().lower()):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Unknown group {group!r}. Try GET /stocks/sectors.",
        )
    if sector and not sectors.is_known_sector(sector):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Unknown sector {sector!r}. Try GET /stocks/sectors.",
        )

    query = select(Stock).where(Stock.is_active.is_(True))
    if sector:
        query = query.where(Stock.sector == sector.strip().lower())
    stocks = list((await db.execute(query)).scalars())

    if group and not sector:
        members = {s.upper() for s in await tickers_in_group(db, group)}
        stocks = [stock for stock in stocks if stock.ticker.upper() in members]

    symbols = [stock.ticker for stock in stocks]
    if not symbols:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="That filter matched no tracked symbols.",
        )
    if len(symbols) > MAX_SYMBOLS:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=(
                f"{len(symbols)} symbols would mean {len(symbols)} live requests. "
                f"Narrow with ?sector= to {MAX_SYMBOLS} or fewer."
            ),
        )

    # The news first: it is a local query, and if nothing is positive there is
    # no point spending a live request per symbol on the scanner.
    news = await shortlist.positive_news(db, hours=hours, min_score=min_score)
    if not news:
        return {
            "rows": [],
            "matched": 0,
            "scanned": 0,
            "news_symbols": 0,
            "window_hours": hours,
            "min_score": min_score,
            "note": (
                f"No stored article scored at or above {min_score} in the last "
                f"{hours} hours, so there was nothing to scan for. Widen with "
                "?hours= or ?min_score=, or check GET /admin/diagnose/sources "
                "if the news feed itself is empty."
            ),
            "caveat": _CAVEAT,
        }

    # Only symbols with news are worth a live bar request.
    with_news = [symbol for symbol in symbols if symbol.upper() in news]
    scan = await run_scan(
        db,
        with_news,
        account_equity=account_equity,
        risk_fraction=risk_fraction,
        include_delayed=include_delayed,
        tz=tz,
        max_bar_age_minutes=max_bar_age_minutes,
    )

    catalysts = await shortlist.upcoming_catalysts(db)
    rows = shortlist.combine(
        scan["signals"],
        news,
        catalysts,
        names={stock.ticker.upper(): stock.company_name for stock in stocks},
        direction=None if direction == "any" else direction,
    )

    return {
        "rows": rows,
        "matched": len(rows),
        "scanned": scan["scanned"],
        # Separating these is what tells "no good news" from "good news, no
        # setup" from "setups, but the market is shut" — three very different
        # empty lists that otherwise look identical.
        "news_symbols": len(news),
        "signals_found": len(scan["signals"]),
        "stale_markets": scan["stale_markets"],
        "delayed_venues": scan["delayed_venues"],
        "session": scan["session"],
        "window_hours": hours,
        "min_score": min_score,
        "caveat": _CAVEAT,
    }


_CAVEAT = (
    "Not a forecast. A row means a positive story landed and a setup is live "
    "with a defined stop, which is a reason to look at the chart. The intraday "
    "setups have no measured hit rate on this universe, so rows are ordered by "
    "news score rather than by setup quality. Catalysts are shown because they "
    "cut both ways: an earnings date tomorrow is a reason to size down, not a "
    "reason the trade is better."
)
