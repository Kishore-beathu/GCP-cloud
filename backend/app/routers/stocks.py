"""Stock lookup endpoints."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models import NewsArticle, SentimentScore, Stock, StockPrice
from app.routers.news import to_news_out
from app.schemas import PriceOut, StockDetail, StockOut

router = APIRouter(prefix="/stocks", tags=["stocks"])


@router.get("", response_model=list[StockOut], summary="List tracked stocks")
async def list_stocks(
    sector: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
    db: AsyncSession = Depends(get_db),
) -> list[Stock]:
    query = (
        select(Stock)
        .where(Stock.is_active.is_(True))
        .order_by(Stock.ticker)
        .limit(limit)
        .offset(offset)
    )
    if sector:
        query = query.where(Stock.sector == sector)
    return list((await db.execute(query)).scalars())


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
        market_cap=stock.market_cap,
        latest_price=PriceOut.model_validate(latest_price) if latest_price else None,
        recent_news=recent_news,
    )
