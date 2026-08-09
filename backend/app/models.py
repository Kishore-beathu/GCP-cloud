"""SQLAlchemy ORM models for the trading intelligence platform.

Column types stay portable so the same models run on PostgreSQL in production
and SQLite in the test suite. `db/schema.sql` is the PostgreSQL-native
equivalent for teams that prefer to provision the schema by hand.
"""

from __future__ import annotations

import enum
from datetime import datetime

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class Sentiment(str, enum.Enum):
    """Direction of a sentiment score."""

    POSITIVE = "positive"
    NEGATIVE = "negative"
    NEUTRAL = "neutral"


class EventType(str, enum.Enum):
    """Business event a headline describes."""

    FDA_APPROVAL = "fda_approval"
    REVENUE = "revenue"
    MERGER_ACQUISITION = "merger_acquisition"
    LITIGATION = "litigation"
    RECALL = "recall"
    PARTNERSHIP = "partnership"
    CLINICAL_TRIAL = "clinical_trial"
    EXEC_CHANGE = "exec_change"
    FACILITY = "facility"
    ANALYST_RATING = "analyst_rating"
    CAPITAL_RAISE = "capital_raise"
    OTHER = "other"


class AlertType(str, enum.Enum):
    """What an alert watches for."""

    POSITIVE_NEWS = "positive_news"
    NEGATIVE_NEWS = "negative_news"
    SENTIMENT_SPIKE = "sentiment_spike"
    EVENT_TYPE = "event_type"
    PRICE_CHANGE = "price_change"


class Stock(Base):
    """A tracked security."""

    __tablename__ = "stocks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    ticker: Mapped[str] = mapped_column(String(16), unique=True, index=True)
    company_name: Mapped[str] = mapped_column(String(255))
    sector: Mapped[str | None] = mapped_column(String(64), index=True)
    exchange: Mapped[str | None] = mapped_column(String(32))
    cik: Mapped[str | None] = mapped_column(String(16), index=True)
    market_cap: Mapped[float | None] = mapped_column(Float)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default="1")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    articles: Mapped[list["NewsArticle"]] = relationship(
        back_populates="stock", cascade="all, delete-orphan"
    )
    prices: Mapped[list["StockPrice"]] = relationship(
        back_populates="stock", cascade="all, delete-orphan"
    )
    alerts: Mapped[list["UserAlert"]] = relationship(
        back_populates="stock", cascade="all, delete-orphan"
    )


class NewsArticle(Base):
    """A news item, filing, or press release attached to a stock."""

    __tablename__ = "news_articles"
    __table_args__ = (
        # Deduplication key: the same story syndicated by two sources is two rows,
        # but re-ingesting one source's URL is a no-op.
        UniqueConstraint("url", "source", name="uq_news_articles_url_source"),
        Index("ix_news_articles_ticker_published", "ticker_id", "published_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    ticker_id: Mapped[int] = mapped_column(
        ForeignKey("stocks.id", ondelete="CASCADE"), index=True
    )
    headline: Mapped[str] = mapped_column(Text)
    body: Mapped[str | None] = mapped_column(Text)
    source: Mapped[str] = mapped_column(String(64), index=True)
    url: Mapped[str] = mapped_column(String(1024))
    published_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    ingested_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    stock: Mapped[Stock] = relationship(back_populates="articles")
    sentiment: Mapped["SentimentScore | None"] = relationship(
        back_populates="article", cascade="all, delete-orphan", uselist=False
    )


class SentimentScore(Base):
    """Model output for one article. Kept separate so re-scoring is auditable."""

    __tablename__ = "sentiment_scores"
    __table_args__ = (
        UniqueConstraint("article_id", name="uq_sentiment_scores_article"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    article_id: Mapped[int] = mapped_column(
        ForeignKey("news_articles.id", ondelete="CASCADE"), index=True
    )
    sentiment: Mapped[str] = mapped_column(String(16), index=True)
    score: Mapped[float] = mapped_column(Float)
    confidence: Mapped[float] = mapped_column(Float)
    event_type: Mapped[str] = mapped_column(
        String(32), default=EventType.OTHER.value, index=True
    )
    event_confidence: Mapped[float] = mapped_column(Float, default=0.0)
    model_version: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    article: Mapped[NewsArticle] = relationship(back_populates="sentiment")


class StockPrice(Base):
    """One OHLCV bar."""

    __tablename__ = "stock_prices"
    __table_args__ = (
        UniqueConstraint("ticker_id", "price_date", name="uq_stock_prices_ticker_date"),
        Index("ix_stock_prices_ticker_date_desc", "ticker_id", "price_date"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    ticker_id: Mapped[int] = mapped_column(
        ForeignKey("stocks.id", ondelete="CASCADE"), index=True
    )
    open: Mapped[float | None] = mapped_column(Float)
    high: Mapped[float | None] = mapped_column(Float)
    low: Mapped[float | None] = mapped_column(Float)
    close: Mapped[float] = mapped_column(Float)
    volume: Mapped[int | None] = mapped_column(Integer)
    price_date: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    source: Mapped[str] = mapped_column(String(32), default="unknown")

    stock: Mapped[Stock] = relationship(back_populates="prices")


class UserAlert(Base):
    """A standing rule that fires when matching news arrives."""

    __tablename__ = "user_alerts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[str] = mapped_column(String(64), index=True, default="local")
    ticker_id: Mapped[int] = mapped_column(
        ForeignKey("stocks.id", ondelete="CASCADE"), index=True
    )
    alert_type: Mapped[str] = mapped_column(String(32))
    # Free-form threshold payload, e.g. {"min_score": 0.8, "event_type": "fda_approval"}
    condition: Mapped[dict] = mapped_column(JSON, default=dict)
    channels: Mapped[list] = mapped_column(JSON, default=list)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    last_triggered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    stock: Mapped[Stock] = relationship(back_populates="alerts")
    history: Mapped[list["AlertHistory"]] = relationship(
        back_populates="alert", cascade="all, delete-orphan"
    )


class AlertHistory(Base):
    """One firing of an alert. Doubles as the in-app notification feed."""

    __tablename__ = "alert_history"
    __table_args__ = (
        # An article may only fire a given alert once, however often it is re-scored.
        UniqueConstraint("alert_id", "article_id", name="uq_alert_history_alert_article"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    alert_id: Mapped[int] = mapped_column(
        ForeignKey("user_alerts.id", ondelete="CASCADE"), index=True
    )
    article_id: Mapped[int | None] = mapped_column(
        ForeignKey("news_articles.id", ondelete="SET NULL"), index=True
    )
    triggered_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )
    payload: Mapped[dict] = mapped_column(JSON, default=dict)

    alert: Mapped[UserAlert] = relationship(back_populates="history")
