"""Pydantic request and response models."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.models import AlertType, EventType, Sentiment, TradeSide


class ORMModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


# --- Health -----------------------------------------------------------------


class HealthResponse(BaseModel):
    status: str
    timestamp: datetime
    environment: str
    database: str
    sentiment_backend: str
    # The commit this process is running, so "did my pull reach the server"
    # is one request rather than an inference from surprising output. Code is
    # loaded once at startup, so a pulled fix does nothing until a restart,
    # and every symptom of that looks like the fix not working.
    build: str | None = None


# --- Stocks -----------------------------------------------------------------


class StockBase(ORMModel):
    ticker: str
    company_name: str
    sector: str | None = None
    exchange: str | None = None
    mic: str | None = None
    region: str | None = None
    country: str | None = None
    # ISO 4217, except "GBp" for London, whose prices are quoted in pence.
    currency: str | None = None
    market_cap: float | None = None


class StockOut(StockBase):
    id: int
    # Derived from `sector`, not stored: the grouping is a presentation choice
    # and re-grouping should never need a migration. See services/sectors.py.
    sector_group: str = "other"
    # The last stored close, so a list renders prices immediately rather than
    # waiting on a WebSocket push that only covers the subscribed few.
    last_price: float | None = None
    last_change_pct: float | None = None
    last_price_date: datetime | None = None


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
    # How many other sources carried the same story. Several wires running one
    # release within minutes is corroboration, so it is shown rather than
    # silently discarded.
    corroborations: int = 0
    other_sources: list[str] = Field(default_factory=list)


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


# --- Portfolio --------------------------------------------------------------


class PortfolioCreate(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    starting_cash: float = Field(default=100_000.0, gt=0)
    user_id: str = "local"


class PortfolioOut(ORMModel):
    id: int
    user_id: str
    name: str
    starting_cash: float
    cash: float
    created_at: datetime


class TradeCreate(BaseModel):
    ticker: str
    side: TradeSide
    quantity: float = Field(gt=0)
    # Omit to fill at the latest stored close.
    price: float | None = Field(default=None, gt=0)
    rationale: str = "manual"

    @field_validator("ticker")
    @classmethod
    def _normalise_ticker(cls, value: str) -> str:
        ticker = value.strip().upper()
        if not ticker:
            raise ValueError("ticker must not be empty")
        return ticker


class TradeOut(ORMModel):
    id: int
    ticker: str
    side: TradeSide
    quantity: float
    price: float
    executed_at: datetime
    rationale: str | None = None


class PositionOut(BaseModel):
    ticker: str
    quantity: float
    average_cost: float
    last_price: float | None = None
    market_value: float
    unrealised_pnl: float
    priced: bool = Field(description="False when valued at cost for lack of price history")
    currency: str = Field(default="USD", description="Quote currency; GBp means pence")


class PortfolioDetail(PortfolioOut):
    positions: list[PositionOut] = Field(default_factory=list)
    cash_value: float = 0.0
    positions_value: float = 0.0
    total_value: float = 0.0
    realised_pnl: float = 0.0
    unrealised_pnl: float = 0.0
    total_return_pct: float | None = None
    positions_by_currency: dict[str, float] = Field(
        default_factory=dict, description="Market value per quote currency"
    )
    mixed_currency: bool = Field(
        default=False,
        description="True when holdings span currencies, so total_value is not FX-converted "
        "and should not be read as a single figure",
    )


class SimulationRequest(BaseModel):
    days: int = Field(default=180, ge=1, le=1825)
    min_score: float = Field(default=0.5, ge=0, le=1)
    min_confidence: float = Field(default=0.0, ge=0, le=1)
    position_size_pct: float = Field(default=10.0, gt=0, le=100)
    hold_days: int = Field(default=5, ge=1, le=365)


class SimulationResponse(BaseModel):
    portfolio_id: int
    trades_executed: int
    signals_seen: int
    signals_skipped: int
    skip_reasons: dict[str, int] = Field(default_factory=dict)
    valuation: PortfolioDetail | None = None


# --- WebSocket --------------------------------------------------------------


class TickerUpdate(BaseModel):
    type: str = "price_update"
    ticker: str
    price: float | None = None
    change: float | None = None
    sentiment_trend: Sentiment | None = None
    timestamp: datetime
