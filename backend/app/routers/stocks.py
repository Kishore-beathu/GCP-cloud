"""Stock lookup endpoints."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models import NewsArticle, SentimentScore, Stock, StockPrice
from app.routers.news import to_news_out
from app.services import markets
from app.schemas import PriceOut, StockDetail, StockOut

router = APIRouter(prefix="/stocks", tags=["stocks"])


@router.get("", response_model=list[StockOut], summary="Search the tracked universe")
async def list_stocks(
    q: str | None = Query(
        default=None,
        description="Free text over ticker and company name, e.g. 'novo' or '.T'",
    ),
    sector: str | None = Query(default=None, description="pharma, biotech, cdmo, …"),
    region: str | None = Query(
        default=None, description="north_america, europe, asia_pacific"
    ),
    country: str | None = Query(default=None, description="ISO 3166-1 alpha-2, e.g. JP"),
    mic: str | None = Query(default=None, description="Market identifier code, e.g. XLON"),
    currency: str | None = Query(default=None, description="ISO 4217, e.g. EUR"),
    limit: int = Query(default=100, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
    db: AsyncSession = Depends(get_db),
) -> list[Stock]:
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
    return list((await db.execute(query)).scalars())


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
