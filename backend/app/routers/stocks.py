"""Stock lookup endpoints."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.integrations.yahoo import INTRADAY_WINDOWS, YahooUnavailable, fetch_intraday
from app.models import NewsArticle, SentimentScore, Stock, StockPrice
from app.routers.news import to_news_out
from app.services import markets, sectors
from app.schemas import PriceOut, StockDetail, StockOut

router = APIRouter(prefix="/stocks", tags=["stocks"])


@router.get("", response_model=list[StockOut], summary="Search the tracked universe")
async def list_stocks(
    q: str | None = Query(
        default=None,
        description="Free text over ticker and company name, e.g. 'novo' or '.T'",
    ),
    sector: str | None = Query(default=None, description="pharma, biotech, cdmo, …"),
    group: str | None = Query(
        default=None,
        description="Industry group: pharma_life_sciences, ai, data_storage",
    ),
    region: str | None = Query(
        default=None, description="north_america, europe, asia_pacific"
    ),
    country: str | None = Query(default=None, description="ISO 3166-1 alpha-2, e.g. JP"),
    mic: str | None = Query(default=None, description="Market identifier code, e.g. XLON"),
    currency: str | None = Query(default=None, description="ISO 4217, e.g. EUR"),
    limit: int = Query(default=100, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
    db: AsyncSession = Depends(get_db),
) -> list[StockOut]:
    """Filter the universe by market, sector, or free text.

    Filters combine with AND, so ``?region=europe&sector=biotech`` narrows to
    European biotech. All matching is case-insensitive.
    """
    query = select(Stock).where(Stock.is_active.is_(True))

    if q:
        pattern = f"%{q.strip().lower()}%"
        query = query.where(
            or_(
                func.lower(Stock.ticker).like(pattern),
                func.lower(Stock.company_name).like(pattern),
            )
        )
    if sector:
        query = query.where(func.lower(Stock.sector) == sector.strip().lower())
    if group:
        members = sectors.sectors_in(group.strip().lower())
        if not members:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"Unknown group {group!r}. Try GET /stocks/sectors.",
            )
        query = query.where(func.lower(Stock.sector).in_(members))
    if region:
        query = query.where(func.lower(Stock.region) == region.strip().lower())
    if country:
        query = query.where(func.upper(Stock.country) == country.strip().upper())
    if mic:
        query = query.where(func.upper(Stock.mic) == mic.strip().upper())
    if currency:
        # Currency codes are case-sensitive in one place only: London's GBp.
        query = query.where(Stock.currency == currency.strip())

    query = query.order_by(Stock.ticker).limit(limit).offset(offset)
    stocks = list((await db.execute(query)).scalars())
    return await _with_latest_prices(db, stocks)


async def _with_latest_prices(db: AsyncSession, stocks: list[Stock]) -> list[StockOut]:
    """Attach each stock's last close and day-over-day change.

    Without this the only path to a price is a WebSocket push, and the client
    subscribes to a bounded number of symbols — so most of a large watchlist
    showed a dash forever, whatever the database held. Live ticks still take
    precedence in the UI; this is what fills the rest, and what shows anything
    at all before the first push arrives.
    """
    if not stocks:
        return []

    ranked = (
        select(
            StockPrice.ticker_id,
            StockPrice.close,
            StockPrice.price_date,
            func.row_number()
            .over(
                partition_by=StockPrice.ticker_id,
                order_by=StockPrice.price_date.desc(),
            )
            .label("rank"),
        )
        .where(StockPrice.ticker_id.in_([stock.id for stock in stocks]))
        .subquery()
    )

    # Two rows per ticker: the latest close, and the one before it for the change.
    recent: dict[int, list] = {}
    for row in (await db.execute(select(ranked).where(ranked.c.rank <= 2))).all():
        recent.setdefault(row.ticker_id, []).append(row)

    out = []
    for stock in stocks:
        rows = sorted(recent.get(stock.id, []), key=lambda row: row.rank)
        item = StockOut.model_validate(stock)
        item.sector_group = sectors.group_for(stock.sector)
        if rows:
            latest = rows[0]
            item.last_price = latest.close
            item.last_price_date = latest.price_date
            if len(rows) > 1 and rows[1].close:
                item.last_change_pct = round(
                    (latest.close - rows[1].close) / rows[1].close * 100, 4
                )
        out.append(item)
    return out


@router.get("/sectors", summary="Industry groups, with the sectors and counts in each")
async def list_sector_groups(db: AsyncSession = Depends(get_db)) -> dict:
    """The grouping the watchlist navigates by.

    Counts are computed from the tracked universe rather than hard-coded, so a
    group that has quietly emptied is visible instead of implied.
    """
    per_sector = dict(
        (
            await db.execute(
                select(Stock.sector, func.count(Stock.id))
                .where(Stock.is_active.is_(True))
                .group_by(Stock.sector)
            )
        ).all()
    )

    counts: dict[str, int] = {}
    for sector_name, count in per_sector.items():
        key = sectors.group_for(sector_name)
        counts[key] = counts.get(key, 0) + count

    groups = []
    for group in sectors.all_groups():
        tracked = counts.get(group.key, 0)
        if tracked or group.key != sectors.OTHER.key:
            groups.append({**sectors.describe(group), "tracked_symbols": tracked})

    return {"total": sum(counts.values()), "groups": groups}


@router.get("/markets", summary="Venues in the universe, with session state")
async def list_markets(db: AsyncSession = Depends(get_db)) -> dict:
    """Which markets the tracked universe spans, and which are open right now.

    Useful before wondering why the live price stream is quiet: outside a
    venue's session there are simply no trades to stream.
    """
    counts = dict(
        (
            await db.execute(
                select(Stock.mic, func.count(Stock.id))
                .where(Stock.is_active.is_(True))
                .group_by(Stock.mic)
            )
        ).all()
    )

    venues = []
    for market in markets.MARKETS:
        tracked = counts.get(market.mic, 0)
        if tracked:
            venues.append({**markets.describe(market), "tracked_symbols": tracked})

    by_region: dict[str, int] = {}
    for venue in venues:
        by_region[venue["region"]] = by_region.get(venue["region"], 0) + venue["tracked_symbols"]

    return {
        "regions": by_region,
        "open_now": sorted(v["mic"] for v in venues if v["is_open"]),
        "venues": sorted(venues, key=lambda v: (v["region"], v["mic"])),
    }


async def get_stock_or_404(db: AsyncSession, ticker: str) -> Stock:
    """Look up a stock by symbol, raising 404 when it is not tracked."""
    symbol = ticker.strip().upper()
    stock = (
        await db.execute(select(Stock).where(Stock.ticker == symbol))
    ).scalar_one_or_none()
    if stock is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=f"Ticker {symbol} is not tracked"
        )
    return stock


@router.get(
    "/{ticker}/prices",
    response_model=list[PriceOut],
    summary="Daily price history, oldest first",
)
async def get_price_history(
    ticker: str,
    days: int = Query(default=90, ge=1, le=1825),
    db: AsyncSession = Depends(get_db),
) -> list[StockPrice]:
    stock = await get_stock_or_404(db, ticker)
    since = datetime.now(timezone.utc) - timedelta(days=days)
    rows = (
        await db.execute(
            select(StockPrice)
            .where(StockPrice.ticker_id == stock.id, StockPrice.price_date >= since)
            .order_by(StockPrice.price_date)
        )
    ).scalars()
    return list(rows)


@router.get(
    "/{ticker}/intraday",
    summary="Intraday bars for a short window (1h, 1d, 1w)",
)
async def get_intraday(
    ticker: str,
    window: str = Query(default="1d", description="1h, 1d or 1w"),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Bars for the last hour, day or week.

    Served live rather than from `stock_prices`, which holds one row per
    trading day: mixing minute bars into it would redefine "the previous
    close" for the backtester, the portfolio valuation and the watchlist's
    day-over-day change. Responses are cached for a few seconds, so toggling
    ranges does not hammer the upstream.
    """
    stock = await get_stock_or_404(db, ticker)

    if window not in INTRADAY_WINDOWS:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"window must be one of {sorted(INTRADAY_WINDOWS)}",
        )

    try:
        bars = await fetch_intraday(stock.ticker, window)
    except YahooUnavailable as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)
        ) from exc

    _, interval, _, _ = INTRADAY_WINDOWS[window]
    return {
        "ticker": stock.ticker,
        "window": window,
        "interval": interval,
        "currency": stock.currency,
        "points": [{"at": bar.at.isoformat(), "close": bar.close} for bar in bars],
    }


@router.get("/{ticker}", response_model=StockDetail, summary="Stock detail with latest price")
async def get_stock(
    ticker: str,
    news_limit: int = Query(default=10, ge=0, le=100),
    db: AsyncSession = Depends(get_db),
) -> StockDetail:
    stock = await get_stock_or_404(db, ticker)

    latest_price = (
        await db.execute(
            select(StockPrice)
            .where(StockPrice.ticker_id == stock.id)
            .order_by(StockPrice.price_date.desc())
            .limit(1)
        )
    ).scalar_one_or_none()

    recent_news = []
    if news_limit:
        rows = (
            await db.execute(
                select(NewsArticle, SentimentScore)
                .outerjoin(SentimentScore, SentimentScore.article_id == NewsArticle.id)
                .where(NewsArticle.ticker_id == stock.id)
                .order_by(NewsArticle.published_at.desc())
                .limit(news_limit)
            )
        ).all()
        recent_news = [to_news_out(article, stock.ticker, score) for article, score in rows]

    return StockDetail(
        id=stock.id,
        ticker=stock.ticker,
        company_name=stock.company_name,
        sector=stock.sector,
        exchange=stock.exchange,
        mic=stock.mic,
        region=stock.region,
        country=stock.country,
        currency=stock.currency,
        market_cap=stock.market_cap,
        latest_price=PriceOut.model_validate(latest_price) if latest_price else None,
        recent_news=recent_news,
    )
