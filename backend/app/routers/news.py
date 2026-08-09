"""News feed endpoints."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models import EventType, NewsArticle, Sentiment, SentimentScore, Stock
from app.schemas import NewsArticleOut, SentimentOut

router = APIRouter(prefix="/news", tags=["news"])


def to_news_out(article: NewsArticle, ticker: str, score: SentimentScore | None) -> NewsArticleOut:
    """Build the API representation of an article."""
    return NewsArticleOut(
        id=article.id,
        ticker=ticker,
        headline=article.headline,
        source=article.source,
        url=article.url,
        published_at=article.published_at,
        sentiment=SentimentOut.model_validate(score) if score else None,
    )


@router.get("", response_model=list[NewsArticleOut], summary="Search the news feed")
async def list_news(
    ticker: str | None = Query(default=None, description="Filter to one symbol, e.g. MRNA"),
    sentiment: Sentiment | None = Query(default=None),
    event_type: EventType | None = Query(default=None),
    source: str | None = Query(default=None),
    min_score: float | None = Query(default=None, ge=-1.0, le=1.0),
    since_days: int | None = Query(default=None, ge=1, le=3650),
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    db: AsyncSession = Depends(get_db),
) -> list[NewsArticleOut]:
    """Return scored articles, newest first, with optional filters."""
    query = (
        select(NewsArticle, Stock.ticker, SentimentScore)
        .join(Stock, Stock.id == NewsArticle.ticker_id)
        .outerjoin(SentimentScore, SentimentScore.article_id == NewsArticle.id)
        .order_by(NewsArticle.published_at.desc())
        .limit(limit)
        .offset(offset)
    )

    if ticker:
        query = query.where(Stock.ticker == ticker.strip().upper())
    if sentiment:
        query = query.where(SentimentScore.sentiment == sentiment.value)
    if event_type:
        query = query.where(SentimentScore.event_type == event_type.value)
    if source:
        query = query.where(NewsArticle.source == source)
    if min_score is not None:
        query = query.where(SentimentScore.score >= min_score)
    if since_days is not None:
        cutoff = datetime.now(timezone.utc) - timedelta(days=since_days)
        query = query.where(NewsArticle.published_at >= cutoff)

    rows = (await db.execute(query)).all()
    return [to_news_out(article, symbol, score) for article, symbol, score in rows]
