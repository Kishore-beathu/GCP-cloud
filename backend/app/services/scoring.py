"""A ranked, explainable score for every tracked symbol.

The platform could tell you what one story meant. It could not answer the
question a user actually has in front of 163 symbols — *which of these looks
most interesting today* — because nothing compared symbols to each other.

The design borrows the shape of commercial AI-score products (Danelfin and
its peers) and deliberately not their claims:

* **Cross-sectional, not absolute.** Every factor is converted to a percentile
  against the rest of the universe on the same day. "Momentum of +4%" means
  nothing on its own; "stronger 21-day momentum than 85% of the universe"
  does, and it stays meaningful when the whole market moves together.
* **Explainable by construction.** The score is a weighted sum of named
  factors, and every response carries each factor's raw value, its percentile
  and its contribution. There is no model to trust — the arithmetic is on
  screen. Commercial scores report which features mattered; this reports the
  whole calculation.
* **No probability claim.** A product that says "76% chance of beating the
  market in three months" is claiming a calibrated forecast. This score is
  ordinal: it ranks, it does not forecast. Whether a high rank has predicted
  anything on *your* data is a separate, measurable question, answered by
  `scoring.validate()` rather than asserted here.

Two pillars, because they are what the stored data supports: price behaviour
and news. Fundamentals are absent and their absence is stated rather than
papered over — see docs/COMPARISON.md.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import NewsArticle, SentimentScore, Stock, StockPrice
from app.services import sectors, technicals
from app.services.technicals import Technicals


@dataclass(frozen=True)
class Factor:
    """One named input, with everything needed to audit its effect."""

    key: str
    label: str
    # What the factor actually measured, in its own units.
    value: float | None
    # 0-100 against the rest of the universe today.
    percentile: float | None
    weight: float
    # Positive means it pushed the score up.
    contribution: float
    explanation: str

    def as_dict(self) -> dict:
        return {
            "key": self.key,
            "label": self.label,
            "value": self.value,
            "percentile": self.percentile,
            "weight": self.weight,
            "contribution": self.contribution,
            "explanation": self.explanation,
        }


@dataclass
class StockScore:
    """The composite score for one symbol."""

    ticker: str
    company_name: str
    sector_group: str
    score: float
    technical_score: float | None
    sentiment_score: float | None
    rank: int = 0
    universe_size: int = 0
    sector_rank: int = 0
    sector_size: int = 0
    coverage: float = 0.0
    factors: list[Factor] = field(default_factory=list)
    technicals: Technicals = field(default_factory=Technicals)
    news_count_30d: int = 0

    def as_dict(self) -> dict:
        return {
            "ticker": self.ticker,
            "company_name": self.company_name,
            "sector_group": self.sector_group,
            "score": self.score,
            "technical_score": self.technical_score,
            "sentiment_score": self.sentiment_score,
            "rank": self.rank,
            "universe_size": self.universe_size,
            "sector_rank": self.sector_rank,
            "sector_size": self.sector_size,
            "coverage": self.coverage,
            "news_count_30d": self.news_count_30d,
            "factors": [factor.as_dict() for factor in self.factors],
            "technicals": self.technicals.as_dict(),
        }


# Weights sum to 1 within each pillar. They are judgement, not fitted values —
# fitting them on the same history the score is then validated against would
# make the validation meaningless.
TECHNICAL_WEIGHTS: dict[str, tuple[str, float, str]] = {
    "momentum_63d": ("3-month momentum", 0.30, "Trend over a quarter"),
    "momentum_21d": ("1-month momentum", 0.25, "Trend over a month"),
    "vs_sma_50": ("Price vs 50-day average", 0.20, "Above its own trend line"),
    "range_position_52w": ("Position in 52-week range", 0.15, "Near its highs or lows"),
    "volatility_21d": ("Realised volatility", 0.10, "Lower is scored higher"),
}

# Volatility is the one factor where less is better, so its percentile is
# inverted before weighting. Momentum is not: this is a trend-following score
# by construction, and saying so beats hiding it in a sign.
_INVERTED = frozenset({"volatility_21d"})

SENTIMENT_WEIGHTS: dict[str, tuple[str, float, str]] = {
    "sentiment_mean_30d": ("Average news sentiment", 0.45, "Tone over 30 days"),
    "sentiment_trend": ("Sentiment trend", 0.30, "Last 7 days versus the prior 23"),
    "news_volume_30d": ("News volume", 0.15, "How much is being written"),
    "positive_share_30d": ("Positive share", 0.10, "Proportion of stories scored positive"),
}

# How the two pillars combine. Price behaviour is weighted higher because it is
# measured for every symbol with history, while news coverage is uneven — a
# thinly covered listing would otherwise be ranked mostly on noise.
PILLAR_WEIGHTS = {"technical": 0.6, "sentiment": 0.4}

# Below this many sessions the technical pillar is not computed at all. Twenty
# sessions is the shortest window any of its factors needs.
MIN_SESSIONS = 21


@dataclass
class _Raw:
    """Per-symbol inputs, before anything is ranked against anything else."""

    stock: Stock
    technicals: Technicals
    sentiment: dict[str, float | None]
    news_count: int


def percentile_ranks(values: dict[str, float | None]) -> dict[str, float | None]:
    """Convert raw values to 0-100 percentiles within this set.

    Ties share the midpoint of the positions they span, so a factor where most
    symbols report the same number cannot hand a few of them an advantage that
    is an artefact of sort order.
    """
    present = sorted((value, key) for key, value in values.items() if value is not None)
    ranks: dict[str, float | None] = {key: None for key in values}
    if not present:
        return ranks
    if len(present) == 1:
        ranks[present[0][1]] = 50.0
        return ranks

    index = 0
    while index < len(present):
        end = index
        while end + 1 < len(present) and present[end + 1][0] == present[index][0]:
            end += 1
        # Midpoint of the tied block, scaled to 0-100.
        midpoint = (index + end) / 2
        percentile = round(midpoint / (len(present) - 1) * 100, 4)
        for position in range(index, end + 1):
            ranks[present[position][1]] = percentile
        index = end + 1
    return ranks


async def _load_inputs(db: AsyncSession, days: int) -> list[_Raw]:
    """Prices and scored news for every active symbol, in two queries."""
    stocks = list(
        (
            await db.execute(
                select(Stock).where(Stock.is_active.is_(True)).order_by(Stock.ticker)
            )
        ).scalars()
    )
    if not stocks:
        return []

    by_id = {stock.id: stock for stock in stocks}
    since_prices = datetime.now(timezone.utc) - timedelta(days=400)

    price_rows = (
        await db.execute(
            select(StockPrice.ticker_id, StockPrice.price_date, StockPrice.close).where(
                StockPrice.ticker_id.in_(by_id), StockPrice.price_date >= since_prices
            )
        )
    ).all()
    series: dict[int, list[tuple[datetime, float]]] = {}
    for ticker_id, price_date, close in price_rows:
        if close is not None:
            series.setdefault(ticker_id, []).append((price_date, close))

    since_news = datetime.now(timezone.utc) - timedelta(days=days)
    news_rows = (
        await db.execute(
            select(
                NewsArticle.ticker_id,
                NewsArticle.published_at,
                SentimentScore.score,
                SentimentScore.sentiment,
            )
            .join(SentimentScore, SentimentScore.article_id == NewsArticle.id)
            .where(
                NewsArticle.ticker_id.in_(by_id),
                NewsArticle.published_at >= since_news,
                # Syndicated copies would count one event several times.
                NewsArticle.duplicate_of_id.is_(None),
            )
        )
    ).all()

    news: dict[int, list[tuple[datetime, float, str]]] = {}
    for ticker_id, published_at, score, sentiment in news_rows:
        news.setdefault(ticker_id, []).append((published_at, score or 0.0, sentiment))

    return [
        _Raw(
            stock=stock,
            technicals=technicals.compute(series.get(stock.id, [])),
            sentiment=_summarise_news(news.get(stock.id, []), days),
            news_count=len(news.get(stock.id, [])),
        )
        for stock in stocks
    ]


def _summarise_news(
    rows: list[tuple[datetime, float, str]], days: int
) -> dict[str, float | None]:
    """Reduce a symbol's scored news to the four sentiment factors."""
    if not rows:
        # Every factor is unknown, including volume. Returning 0.0 for volume
        # would make "nothing has been written about this" a measured value,
        # so a symbol with no news at all earned a mid-pack sentiment score and
        # a rank, diluting the ranking with symbols nothing is known about.
        return {
            "sentiment_mean_30d": None,
            "sentiment_trend": None,
            "news_volume_30d": None,
            "positive_share_30d": None,
        }

    now = datetime.now(timezone.utc)
    recent_cutoff = now - timedelta(days=7)

    scores = [score for _, score, _ in rows]
    recent = [
        score
        for published, score, _ in rows
        if (published if published.tzinfo else published.replace(tzinfo=timezone.utc))
        >= recent_cutoff
    ]
    older = [
        score
        for published, score, _ in rows
        if (published if published.tzinfo else published.replace(tzinfo=timezone.utc))
        < recent_cutoff
    ]

    mean = sum(scores) / len(scores)
    trend = None
    if recent and older:
        trend = sum(recent) / len(recent) - sum(older) / len(older)

    positives = sum(1 for _, _, sentiment in rows if sentiment == "positive")
    return {
        "sentiment_mean_30d": round(mean, 4),
        "sentiment_trend": round(trend, 4) if trend is not None else None,
        # Volume is scored on a log scale: the difference between 1 and 5
        # stories is meaningful, between 80 and 100 it is not.
        "news_volume_30d": round(_log_volume(len(rows)), 4),
        "positive_share_30d": round(positives / len(rows) * 100, 4),
    }


def _log_volume(count: int) -> float:
    from math import log1p

    return log1p(count)


def _pillar(
    raw: _Raw,
    ranks: dict[str, dict[str, float | None]],
    weights: dict[str, tuple[str, float, str]],
    values: dict[str, float | None],
) -> tuple[float | None, list[Factor]]:
    """Weighted mean of a pillar's available factors, plus their audit trail.

    Weights are renormalised over the factors that are actually present, so a
    symbol missing one indicator is not silently penalised — it is scored on
    what is known about it, and `coverage` reports how much that was.
    """
    factors: list[Factor] = []
    total_weight = 0.0
    weighted = 0.0

    for key, (label, weight, explanation) in weights.items():
        percentile = ranks[key].get(raw.stock.ticker)
        if percentile is None:
            continue
        effective = 100 - percentile if key in _INVERTED else percentile
        total_weight += weight
        weighted += effective * weight
        factors.append(
            Factor(
                key=key,
                label=label,
                value=values.get(key),
                percentile=round(effective, 2),
                weight=weight,
                contribution=round(effective * weight, 3),
                explanation=explanation,
            )
        )

    if not total_weight:
        return None, factors
    return round(weighted / total_weight, 2), factors


async def score_universe(db: AsyncSession, days: int = 30) -> list[StockScore]:
    """Score and rank every active symbol, best first."""
    inputs = await _load_inputs(db, days)
    if not inputs:
        return []

    # Percentiles are computed per factor across the whole universe, which is
    # what makes the score a comparison rather than a threshold.
    technical_values = {
        key: {
            raw.stock.ticker: (
                getattr(raw.technicals, key) if raw.technicals.sessions >= MIN_SESSIONS else None
            )
            for raw in inputs
        }
        for key in TECHNICAL_WEIGHTS
    }
    sentiment_values = {
        key: {raw.stock.ticker: raw.sentiment.get(key) for raw in inputs}
        for key in SENTIMENT_WEIGHTS
    }

    technical_ranks = {key: percentile_ranks(values) for key, values in technical_values.items()}
    sentiment_ranks = {key: percentile_ranks(values) for key, values in sentiment_values.items()}

    scored: list[StockScore] = []
    for raw in inputs:
        technical, technical_factors = _pillar(
            raw,
            technical_ranks,
            TECHNICAL_WEIGHTS,
            {key: getattr(raw.technicals, key) for key in TECHNICAL_WEIGHTS},
        )
        sentiment, sentiment_factors = _pillar(
            raw, sentiment_ranks, SENTIMENT_WEIGHTS, raw.sentiment
        )

        available = {
            "technical": technical,
            "sentiment": sentiment,
        }
        present = {name: value for name, value in available.items() if value is not None}
        if not present:
            continue

        weight_total = sum(PILLAR_WEIGHTS[name] for name in present)
        composite = sum(PILLAR_WEIGHTS[name] * value for name, value in present.items())
        composite = round(composite / weight_total, 2)

        scored.append(
            StockScore(
                ticker=raw.stock.ticker,
                company_name=raw.stock.company_name,
                sector_group=sectors.group_for(raw.stock.sector),
                score=composite,
                technical_score=technical,
                sentiment_score=sentiment,
                # What share of the intended inputs this score actually used.
                coverage=round(weight_total, 2),
                factors=sorted(
                    technical_factors + sentiment_factors,
                    key=lambda factor: factor.contribution,
                    reverse=True,
                ),
                technicals=raw.technicals,
                news_count_30d=raw.news_count,
            )
        )

    scored.sort(key=lambda item: item.score, reverse=True)
    for position, item in enumerate(scored, start=1):
        item.rank = position
        item.universe_size = len(scored)

    # Rank within the industry group too: the best-placed data-storage name is
    # a different question from the best-placed name overall, and a score built
    # on momentum will otherwise just report whichever sector is in favour.
    by_group: dict[str, list[StockScore]] = {}
    for item in scored:
        by_group.setdefault(item.sector_group, []).append(item)
    for group_items in by_group.values():
        for position, item in enumerate(group_items, start=1):
            item.sector_rank = position
            item.sector_size = len(group_items)

    return scored


# --- Validation --------------------------------------------------------------
# A score nobody has tested is decoration. Commercial products publish a
# backtested track record; this measures the same thing on *your* data and
# reports it whatever it says, including "not enough history to tell".


@dataclass
class BucketResult:
    """Forward performance of one slice of the ranking."""

    label: str
    symbols: int
    mean_forward_return: float | None
    median_forward_return: float | None
    win_rate: float | None

    def as_dict(self) -> dict:
        return {
            "label": self.label,
            "symbols": self.symbols,
            "mean_forward_return": self.mean_forward_return,
            "median_forward_return": self.median_forward_return,
            "win_rate": self.win_rate,
        }


def _median(values: list[float]) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[middle]
    return (ordered[middle - 1] + ordered[middle]) / 2


async def validate(
    db: AsyncSession, as_of_days_ago: int = 30, horizon_days: int = 21
) -> dict:
    """Did a high score, some days ago, precede a better return since?

    Scores the universe as it stood ``as_of_days_ago`` days back using only
    price history available *then*, then measures what each symbol did over the
    following ``horizon_days``. Splitting into quintiles answers the only
    question that matters about a ranking: does the top of it outperform the
    bottom?

    This is a single-period test, not a rolling backtest over many start dates,
    so it is evidence rather than proof — one favourable month can flatter any
    ranking. It is reported with the sample size so the reader can weigh it.
    """
    now = datetime.now(timezone.utc)
    as_of = now - timedelta(days=as_of_days_ago)
    horizon_end = as_of + timedelta(days=horizon_days)

    stocks = list(
        (
            await db.execute(
                select(Stock).where(Stock.is_active.is_(True)).order_by(Stock.ticker)
            )
        ).scalars()
    )
    by_id = {stock.id: stock for stock in stocks}
    if not by_id:
        return {"status": "no_stocks", "buckets": []}

    rows = (
        await db.execute(
            select(StockPrice.ticker_id, StockPrice.price_date, StockPrice.close).where(
                StockPrice.ticker_id.in_(by_id)
            )
        )
    ).all()

    history: dict[int, list[tuple[datetime, float]]] = {}
    for ticker_id, price_date, close in rows:
        if close is None:
            continue
        moment = price_date if price_date.tzinfo else price_date.replace(tzinfo=timezone.utc)
        history.setdefault(ticker_id, []).append((moment, close))

    ranked: list[tuple[str, float]] = []
    forward: dict[str, float] = {}

    for ticker_id, series in history.items():
        series.sort(key=lambda row: row[0])
        # Only what was knowable at the time: using later prices to build the
        # score being tested is the classic way to backtest a fantasy.
        past = [row for row in series if row[0] <= as_of]
        after = [row for row in series if as_of < row[0] <= horizon_end]
        if len(past) < MIN_SESSIONS or not after:
            continue

        indicators = technicals.compute(past)
        if indicators.momentum_21d is None:
            continue

        start_price = past[-1][1]
        end_price = after[-1][1]
        if not start_price:
            continue

        symbol = by_id[ticker_id].ticker
        ranked.append((symbol, indicators.momentum_21d))
        forward[symbol] = round((end_price - start_price) / start_price * 100, 4)

    if len(ranked) < 10:
        return {
            "status": "insufficient_history",
            "detail": (
                f"Only {len(ranked)} symbols had {MIN_SESSIONS}+ sessions before "
                f"{as_of.date()} and a price after it. Load more history with "
                "POST /admin/ingest/yahoo?range=2y&only_missing=false."
            ),
            "buckets": [],
        }

    ranked.sort(key=lambda row: row[1], reverse=True)

    # When every symbol scores the same, the order is arbitrary and the buckets
    # are whatever the sort happened to produce — which can report a large,
    # entirely spurious spread. Say there was nothing to separate instead.
    if ranked[0][1] == ranked[-1][1]:
        return {
            "status": "no_dispersion",
            "as_of": as_of.date().isoformat(),
            "symbols_tested": len(ranked),
            "detail": (
                "Every symbol scored identically at the cut-off, so the ranking "
                "had nothing to separate and any spread would be an artefact of "
                "sort order. This usually means the price history is flat or "
                "synthetic."
            ),
            "buckets": [],
        }

    size = max(1, len(ranked) // 5)
    buckets: list[BucketResult] = []
    labels = ["Top 20%", "2nd", "3rd", "4th", "Bottom 20%"]

    for index, label in enumerate(labels):
        start = index * size
        end = len(ranked) if index == len(labels) - 1 else (index + 1) * size
        slice_symbols = [symbol for symbol, _ in ranked[start:end]]
        returns = [forward[symbol] for symbol in slice_symbols if symbol in forward]
        if not returns:
            continue
        buckets.append(
            BucketResult(
                label=label,
                symbols=len(returns),
                mean_forward_return=round(sum(returns) / len(returns), 4),
                median_forward_return=round(_median(returns) or 0.0, 4),
                win_rate=round(
                    sum(1 for value in returns if value > 0) / len(returns) * 100, 2
                ),
            )
        )

    spread = None
    if len(buckets) >= 2 and buckets[0].mean_forward_return is not None:
        bottom = buckets[-1].mean_forward_return
        if bottom is not None:
            spread = round(buckets[0].mean_forward_return - bottom, 4)

    return {
        "status": "ok",
        "as_of": as_of.date().isoformat(),
        "horizon_days": horizon_days,
        "symbols_tested": len(ranked),
        # The number that matters: top quintile minus bottom quintile. Near
        # zero or negative means the ranking did not separate anything on this
        # sample, which is a legitimate and useful answer.
        "top_minus_bottom": spread,
        "caveat": (
            "One start date, one horizon, no transaction costs, survivorship not "
            "controlled. Evidence, not proof."
        ),
        "buckets": [bucket.as_dict() for bucket in buckets],
    }
