"""Pydantic request and response models."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.models import AlertType, EventType, Sentiment


class ORMModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


# --- Health -----------------------------------------------------------------


class HealthResponse(BaseModel):
    status: str
    timestamp: datetime
    environment: str
    database: str
    sentiment_backend: str


# --- Stocks -----------------------------------------------------------------


class StockBase(ORMModel):
    ticker: str
    company_name: str
    sector: str | None = None
    exchange: str | None = None
    market_cap: float | None = None


class StockOut(StockBase):
    id: int


class PriceOut(ORMModel):
    close: float
    open: float | None = None
    high: float | None = None
    low: float | None = None
    volume: int | None = None
    price_date: datetime
    source: str


# --- News -------------------------------------------------------------------


class SentimentOut(ORMModel):
    sentiment: Sentiment
    score: float
    confidence: float
    event_type: EventType
    event_confidence: float
    model_version: str


class NewsArticleOut(ORMModel):
    id: int
    ticker: str
    headline: str
    source: str
    url: str
    published_at: datetime
    sentiment: SentimentOut | None = None


class StockDetail(StockOut):
    latest_price: PriceOut | None = None
    recent_news: list[NewsArticleOut] = Field(default_factory=list)


# --- Alerts -----------------------------------------------------------------


class AlertCreate(BaseModel):
    ticker: str
    alert_type: AlertType
    condition: dict[str, Any] = Field(default_factory=dict)
    channels: list[str] = Field(default_factory=lambda: ["in_app"])
    user_id: str = "local"

    @field_validator("ticker")
    @classmethod
    def _normalise_ticker(cls, value: str) -> str:
        ticker = value.strip().upper()
        if not ticker:
            raise ValueError("ticker must not be empty")
        return ticker


class AlertOut(ORMModel):
    id: int
    user_id: str
    ticker: str
    alert_type: AlertType
    condition: dict[str, Any]
    channels: list[str]
    is_active: bool
    created_at: datetime
    last_triggered_at: datetime | None = None


class AlertHistoryOut(ORMModel):
    id: int
    alert_id: int
    article_id: int | None
    triggered_at: datetime
    payload: dict[str, Any]


# --- Backtest ---------------------------------------------------------------


class EventImpact(BaseModel):
    event_type: EventType
    sentiment: Sentiment
    count: int
    avg_impact_1d: float | None = None
    avg_impact_5d: float | None = None
    avg_impact_30d: float | None = None
    accuracy: float | None = Field(
        default=None,
        description="Share of samples where price moved in the sentiment's direction "
        "over the 5-day window, 0-100.",
    )


class BacktestResponse(BaseModel):
    ticker: str
    period_days: int
    articles_analysed: int
    articles_with_price_data: int
    overall_sentiment_accuracy: float | None = None
    analysis: list[EventImpact] = Field(default_factory=list)


# --- WebSocket --------------------------------------------------------------


class TickerUpdate(BaseModel):
    type: str = "price_update"
    ticker: str
    price: float | None = None
    change: float | None = None
    sentiment_trend: Sentiment | None = None
    timestamp: datetime
