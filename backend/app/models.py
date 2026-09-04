"""SQLAlchemy ORM models for the trading intelligence platform.

Column types stay portable so the same models run on PostgreSQL in production
and SQLite in the test suite. `db/schema.sql` is the PostgreSQL-native
equivalent for teams that prefer to provision the schema by hand.
"""

from __future__ import annotations

import enum
from datetime import date, datetime

from sqlalchemy import (
    JSON,
    Boolean,
    Date,
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
    # Vendor symbol including any venue suffix: PFE, AZN.L, 7203.T, SHOP.TO.
    ticker: Mapped[str] = mapped_column(String(24), unique=True, index=True)
    company_name: Mapped[str] = mapped_column(String(255))
    sector: Mapped[str | None] = mapped_column(String(64), index=True)
    exchange: Mapped[str | None] = mapped_column(String(64))
    # Derived from the ticker suffix at seed time (app/services/markets.py) so
    # region and currency are filterable without re-parsing symbols per query.
    mic: Mapped[str | None] = mapped_column(String(8), index=True)
    region: Mapped[str | None] = mapped_column(String(16), index=True)
    country: Mapped[str | None] = mapped_column(String(2), index=True)
    # ISO 4217, except London's GBp: prices there are quoted in pence.
    currency: Mapped[str | None] = mapped_column(String(3))
    cik: Mapped[str | None] = mapped_column(String(16), index=True)
    # Market cap has been on this table since the first migration and nothing
    # ever wrote to it, so every row read NULL while the API cheerfully served
    # the field. Populated from the vendor profile now — see
    # services/fundamentals.py — which is what makes a small/mid-cap universe
    # filter expressible at all.
    market_cap: Mapped[float | None] = mapped_column(Float)
    shares_outstanding: Mapped[float | None] = mapped_column(Float)
    # When the two above were last refreshed. Stored because a market cap of
    # unknown age is worse than none: it invites a filter that silently uses
    # last quarter's share count.
    fundamentals_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
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
    # Set when this row is another wire's copy of a story already stored. The
    # earliest copy is the primary and keeps duplicate_of_id NULL. Duplicates
    # are kept rather than dropped: that four wires carried the same release
    # within a minute is itself signal, and the feed can show it as
    # corroboration instead of as four identical rows.
    duplicate_of_id: Mapped[int | None] = mapped_column(
        ForeignKey("news_articles.id", ondelete="SET NULL"), nullable=True, index=True
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


class TradeSide(str, enum.Enum):
    """Direction of a simulated trade."""

    BUY = "buy"
    SELL = "sell"


class Portfolio(Base):
    """A paper-trading account. Cash is tracked here; holdings derive from trades."""

    __tablename__ = "portfolios"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[str] = mapped_column(String(64), index=True, default="local")
    name: Mapped[str] = mapped_column(String(128))
    starting_cash: Mapped[float] = mapped_column(Float, default=100_000.0)
    cash: Mapped[float] = mapped_column(Float, default=100_000.0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    trades: Mapped[list["Trade"]] = relationship(
        back_populates="portfolio", cascade="all, delete-orphan"
    )


class Trade(Base):
    """One simulated fill. The trade log is the source of truth for positions."""

    __tablename__ = "trades"
    __table_args__ = (Index("ix_trades_portfolio_executed", "portfolio_id", "executed_at"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    portfolio_id: Mapped[int] = mapped_column(
        ForeignKey("portfolios.id", ondelete="CASCADE"), index=True
    )
    ticker_id: Mapped[int] = mapped_column(
        ForeignKey("stocks.id", ondelete="CASCADE"), index=True
    )
    side: Mapped[str] = mapped_column(String(8))
    quantity: Mapped[float] = mapped_column(Float)
    price: Mapped[float] = mapped_column(Float)
    executed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )
    # Why the trade happened: "manual", or the signal that generated it.
    rationale: Mapped[str | None] = mapped_column(String(255))

    # The plan, as numbers rather than prose. These used to live inside
    # `rationale`, where nothing could read them — so a position whose stop was
    # breached while nobody was watching simply stayed open, and the log
    # recorded a loss the plan had said to cut. Stored here, the exit monitor
    # can act on them.
    stop: Mapped[float | None] = mapped_column(Float)
    target: Mapped[float | None] = mapped_column(Float)
    # Which setup produced the entry, so the log can be grouped by setup when
    # the hit rate is eventually counted.
    setup: Mapped[str | None] = mapped_column(String(64), index=True)

    portfolio: Mapped[Portfolio] = relationship(back_populates="trades")
    stock: Mapped[Stock] = relationship()


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


class EarningsReport(Base):
    """One reported quarter: what was expected, and what arrived.

    Earnings surprise and the drift that follows it are among the most
    replicated effects in the published literature, and this platform had no
    way to express either — the news pipeline could see that a company
    *reported*, never whether the number beat.
    """

    __tablename__ = "earnings_reports"
    __table_args__ = (
        UniqueConstraint("ticker_id", "period", name="uq_earnings_ticker_period"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    ticker_id: Mapped[int] = mapped_column(
        ForeignKey("stocks.id", ondelete="CASCADE"), index=True
    )
    # The fiscal period the figures describe, as the vendor dates it.
    period: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    reported_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), index=True
    )
    eps_actual: Mapped[float | None] = mapped_column(Float)
    eps_estimate: Mapped[float | None] = mapped_column(Float)
    revenue_actual: Mapped[float | None] = mapped_column(Float)
    revenue_estimate: Mapped[float | None] = mapped_column(Float)
    # Surprise as a percentage of the estimate. Stored rather than derived on
    # read because the denominator needs care: an estimate of zero or a
    # negative one makes the ordinary formula meaningless, and that judgement
    # belongs in one place.
    eps_surprise_pct: Mapped[float | None] = mapped_column(Float)
    source: Mapped[str] = mapped_column(String(32), default="finnhub")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    stock: Mapped[Stock] = relationship()


class InsiderTransaction(Base):
    """One open-market insider trade, from a Form 4.

    Individually these move nothing, which is why the news firehose filters
    them out — a single officer selling on a schedule is noise, and the feed
    carries thousands a day. In aggregate they are different: a cluster of
    officers buying their own stock on the open market is one of the
    better-evidenced signals available, and it is information no news feed
    carries because it is not news.

    Only open-market purchases and sales are stored. A grant, an option
    exercise or a tax withholding is a compensation event that says nothing
    about what anyone thinks the stock is worth, and mixing them in would swamp
    the deliberate trades with mechanical ones.
    """

    __tablename__ = "insider_transactions"
    __table_args__ = (
        # An accession can hold several transactions; the sequence
        # distinguishes them. Together they make re-ingesting a filing a no-op.
        UniqueConstraint(
            "accession", "sequence", name="uq_insider_accession_sequence"
        ),
        Index("ix_insider_ticker_traded", "ticker_id", "traded_on"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    ticker_id: Mapped[int] = mapped_column(
        ForeignKey("stocks.id", ondelete="CASCADE"), index=True
    )
    accession: Mapped[str] = mapped_column(String(32), index=True)
    sequence: Mapped[int] = mapped_column(Integer, default=0)

    # The trade date, not the filing date. Form 4 is due within two business
    # days, and ranking on when the paperwork arrived would put a Monday trade
    # and a Wednesday trade in different weeks.
    traded_on: Mapped[date] = mapped_column(Date, index=True)
    filed_on: Mapped[date | None] = mapped_column(Date)

    insider_name: Mapped[str | None] = mapped_column(String(255))
    insider_title: Mapped[str | None] = mapped_column(String(255))
    # P (open-market purchase) or S (open-market sale).
    transaction_code: Mapped[str] = mapped_column(String(2), index=True)
    shares: Mapped[float | None] = mapped_column(Float)
    price_per_share: Mapped[float | None] = mapped_column(Float)
    # Signed: positive for an acquisition, negative for a disposal, so summing
    # a symbol's rows gives net conviction without re-reading the code.
    value: Mapped[float | None] = mapped_column(Float)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class ValuationSnapshot(Base):
    """Valuation and quality ratios for one symbol, as at one date.

    A snapshot table rather than columns on ``stocks`` for one reason: a
    backtest has to know what a ratio *was*, not what it is. Overwriting a
    P/E in place would make every historical ranking use today's figure, which
    is the same lookahead the earnings-surprise ranking goes out of its way to
    avoid.

    Ratios are stored as the vendor reports them, including negatives and
    nulls. A loss-making company has a negative P/E and that is true; a company
    with no debt has no EV/EBITDA and that is true as well. Substituting a
    number for either would produce a rank out of an absence.
    """

    __tablename__ = "valuation_snapshots"
    __table_args__ = (
        UniqueConstraint(
            "ticker_id", "captured_on", name="uq_valuation_ticker_captured"
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    ticker_id: Mapped[int] = mapped_column(
        ForeignKey("stocks.id", ondelete="CASCADE"), index=True
    )
    # The date the vendor was asked, not a fiscal period: these are trailing
    # and annual figures that move whenever the vendor recomputes them.
    captured_on: Mapped[date] = mapped_column(Date, index=True)

    pe_ratio: Mapped[float | None] = mapped_column(Float)
    ps_ratio: Mapped[float | None] = mapped_column(Float)
    pb_ratio: Mapped[float | None] = mapped_column(Float)
    ev_ebitda: Mapped[float | None] = mapped_column(Float)
    gross_margin: Mapped[float | None] = mapped_column(Float)
    revenue_growth_yoy: Mapped[float | None] = mapped_column(Float)
    return_on_equity: Mapped[float | None] = mapped_column(Float)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class AnalystTrend(Base):
    """A month's analyst recommendation mix for one symbol.

    A free stand-in for estimate revisions, which need a paid feed. The counts
    themselves are near-useless — analysts are permanently bullish in
    aggregate — but the *change* between months is a real signal about which
    way opinion is moving, and that is what the scoring reads.
    """

    __tablename__ = "analyst_trends"
    __table_args__ = (
        UniqueConstraint("ticker_id", "period", name="uq_analyst_trend_ticker_period"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    ticker_id: Mapped[int] = mapped_column(
        ForeignKey("stocks.id", ondelete="CASCADE"), index=True
    )
    period: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    strong_buy: Mapped[int] = mapped_column(Integer, default=0)
    buy: Mapped[int] = mapped_column(Integer, default=0)
    hold: Mapped[int] = mapped_column(Integer, default=0)
    sell: Mapped[int] = mapped_column(Integer, default=0)
    strong_sell: Mapped[int] = mapped_column(Integer, default=0)
    source: Mapped[str] = mapped_column(String(32), default="finnhub")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    stock: Mapped[Stock] = relationship()


class CatalystEvent(Base):
    """Something scheduled to happen, with a date in the future.

    Everything else stored here is backward-looking: what was filed, what was
    published, what the price did. A catalyst is the opposite — it is known in
    advance and unresolved, which is the only kind of information that answers
    "what should I be watching tomorrow".
    """

    __tablename__ = "catalyst_events"
    __table_args__ = (
        UniqueConstraint(
            "ticker_id", "kind", "expected_at", "external_id",
            name="uq_catalyst_identity",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    ticker_id: Mapped[int] = mapped_column(
        ForeignKey("stocks.id", ondelete="CASCADE"), index=True
    )
    # "earnings", "trial_readout". Deliberately a string rather than an enum:
    # the set will grow as sources are added, and a migration per catalyst type
    # would discourage adding them.
    kind: Mapped[str] = mapped_column(String(32), index=True)
    expected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    # How firmly the date is known. An earnings date confirmed by the company
    # is not the same claim as a trial's estimated primary completion, and a
    # calendar that presents them identically is misleading about both.
    confidence: Mapped[str] = mapped_column(String(16), default="estimated")
    title: Mapped[str] = mapped_column(String(512))
    detail: Mapped[str | None] = mapped_column(Text)
    url: Mapped[str | None] = mapped_column(String(1024))
    # The vendor's own identifier, so re-ingesting updates rather than
    # duplicating when a date moves.
    external_id: Mapped[str] = mapped_column(String(128), default="")
    source: Mapped[str] = mapped_column(String(32), index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    stock: Mapped[Stock] = relationship()


class IntradayBar(Base):
    """One intraday bar, stored so a setup can be measured after the fact.

    Deliberately a separate table from ``stock_prices``, which holds exactly
    one row per (ticker, trading day). The backtester, the portfolio valuation
    and the watchlist's day-over-day change all read "the previous row" as
    "the previous close", and writing minute bars into it would quietly
    redefine that for every one of them.

    The reason this table exists at all: the intraday setups could not be
    validated, because there was no record of what a five-minute chart looked
    like at 09:47 last Tuesday. A scanner whose hit rate cannot be measured is
    an opinion. Accumulating bars is the prerequisite for turning it into a
    number.
    """

    __tablename__ = "intraday_bars"
    __table_args__ = (
        # A run re-fetches the whole session rather than only new bars, so a
        # missed run heals itself on the next one. That only works if storing
        # a bar twice is a no-op.
        UniqueConstraint(
            "ticker_id", "interval", "at", name="uq_intraday_bars_point"
        ),
        Index("ix_intraday_bars_ticker_at", "ticker_id", "at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    ticker_id: Mapped[int] = mapped_column(
        ForeignKey("stocks.id", ondelete="CASCADE"), index=True
    )
    # "5m", "1m". Stored per row rather than assumed, because a series
    # recorded at one interval cannot be compared with one recorded at another
    # and silently mixing them would corrupt every average built from them.
    interval: Mapped[str] = mapped_column(String(8), default="5m", index=True)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)

    open: Mapped[float | None] = mapped_column(Float)
    high: Mapped[float | None] = mapped_column(Float)
    low: Mapped[float | None] = mapped_column(Float)
    close: Mapped[float] = mapped_column(Float)
    # Null means unknown, not zero. A zero-volume bar reads as a halt and
    # drags every relative-volume average down with it.
    volume: Mapped[int | None] = mapped_column(Integer)

    source: Mapped[str] = mapped_column(String(32), default="yahoo")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    stock: Mapped[Stock] = relationship()
