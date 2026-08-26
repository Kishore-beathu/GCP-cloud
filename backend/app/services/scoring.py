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

Three pillars: price behaviour, news, and earnings surprise with analyst
revisions. The third was added weightless and stayed that way until
`validate()` measured it over twelve periods — see PILLAR_WEIGHTS. Valuation
is still absent and its absence is stated rather than papered over; see
docs/COMPARISON.md.

The news pillar's weight is not fixed: it scales with how many articles stand
behind it, so a symbol with one story is ranked mostly on its price rather than
on a percentile that is mostly sort order. `coverage` and
`sentiment_confidence` report how much of the intended input each score
actually used.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import EarningsReport, NewsArticle, SentimentScore, Stock, StockPrice
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
    # The fine-grained sector as well as its group. Ranking cohorts are groups
    # — eleven sectors would leave several with too few members to percentile
    # against — but filtering wants the finer one, because "the clinical-stage
    # names" is a question the group heading cannot answer.
    sector: str | None
    score: float
    technical_score: float | None
    sentiment_score: float | None
    # 0-1: how much of the sentiment pillar's weight the news volume earned.
    sentiment_confidence: float = 0.0
    # 0-1: share of the technical factors that could be computed at all. Below
    # 1 means the symbol lacks the price history one of them needs — the
    # 52-week range wants 252 sessions — so it was ranked without that factor
    # and the rest were reweighted to fill the gap.
    technical_coverage: float = 0.0
    # Reported, not blended. See FUNDAMENTAL_WEIGHTS.
    fundamental_score: float | None = None
    # Kept out of `factors` on purpose. That list is the score's arithmetic —
    # every entry's contribution is part of the number — and these contribute
    # nothing yet. Mixed in, a 0.55-weighted surprise would sort above every
    # technical factor in a "what moved this score" view while moving it by
    # zero, which is exactly the kind of confident-looking wrong thing this
    # design exists to avoid.
    fundamental_factors: list[Factor] = field(default_factory=list)
    rank: int = 0
    universe_size: int = 0
    # Named "sector" for the API's sake, but scoped to the *group* — see the
    # cohort loop in score_universe. Ranking against eleven sectors would leave
    # several of them with two or three members, and a percentile over three
    # symbols is not a percentile.
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
            "sector": self.sector,
            "score": self.score,
            "technical_score": self.technical_score,
            "sentiment_score": self.sentiment_score,
            "sentiment_confidence": self.sentiment_confidence,
            "technical_coverage": self.technical_coverage,
            "fundamental_score": self.fundamental_score,
            "fundamental_factors": [factor.as_dict() for factor in self.fundamental_factors],
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
# Set by measurement, not by assumption — see scoring.validate() and the
# reasoning in FUNDAMENTAL_WEIGHTS below. Over twelve periods on this universe
# the fundamental ranking was the only one whose top-minus-bottom spread was
# distinguishable from zero (t 4.3, positive in 11 of 12 periods); technical
# and sentiment both sat inside their own noise, and sentiment's mean spread
# was negative.
#
# Sentiment keeps a reduced weight rather than none. Its negative result is not
# statistically significant, and it ranks only about a third of the universe,
# so "not shown to work" is the honest reading rather than "shown not to" —
# and zeroing it would also discard news volume, which is the only signal here
# that a symbol is being talked about at all.
PILLAR_WEIGHTS = {"technical": 0.5, "sentiment": 0.15, "fundamental": 0.35}

# The weight above is what the sentiment pillar earns at *full* coverage. Held
# flat it says a symbol with one article deserves the same 40% of its rank as a
# symbol with forty, which is the opposite of what the number is worth: one
# story is an anecdote whose percentile is mostly sort order. So the pillar's
# weight ramps with the evidence behind it and reaches 0.4 at this many
# articles. This is a statement about information, not a fitted parameter — it
# is deliberately not tuned against the validation window it is measured in.
SENTIMENT_FULL_WEIGHT_ARTICLES = 5

# The fundamental factors, and how they earned their pillar weight.
#
# Earnings surprise and analyst revisions have stronger published evidence
# behind them than anything else here — post-earnings drift and revisions
# momentum are among the most replicated effects in the literature. That was a
# reason to measure them, not to trust them: the sentiment pillar was given 0.4
# on the equally reasonable assumption that news matters, and twelve periods
# later the honest reading was that it had not been shown to.
#
# So they shipped computed, reported and ranked as their own strategy inside
# validate(), carrying no weight, explicitly pending that measurement. The
# measurement came back: mean spread +3.21 over twelve periods, positive in
# eleven of them, standard deviation 2.61 against technical's 10.73. The
# obvious objection — that it ranks half as many symbols — runs the wrong way,
# since fewer names per quintile makes bucket means noisier, not steadier.
#
# What that does not settle: the periods are sequential rather than independent
# draws and share a market regime, the universe is today's active symbols so
# anything delisted mid-window is missing, and the vendor supplies only about
# four months of analyst trends, so the earliest periods rank on earnings
# surprise alone. Re-run validate() as history accumulates.
FUNDAMENTAL_WEIGHTS: dict[str, tuple[str, float, str]] = {
    "earnings_surprise_pct": ("Earnings surprise", 0.55, "Last quarter versus consensus"),
    "analyst_revision": ("Analyst revision", 0.45, "Opinion shift over the last month"),
}

# Below this many sessions the technical pillar is not computed at all. Twenty
# sessions is the shortest window any of its factors needs.
MIN_SESSIONS = 21


def sentiment_confidence(news_count: int) -> float:
    """How much of the sentiment pillar's weight this much news has earned.

    Linear from nothing to full weight, because there is no basis in the data
    for a more elaborate curve and inventing one would be false precision.
    """
    if news_count <= 0:
        return 0.0
    return min(1.0, news_count / SENTIMENT_FULL_WEIGHT_ARTICLES)


def _blend(
    technical: float | None,
    sentiment: float | None,
    news_count: int,
    fundamental: float | None = None,
) -> tuple[float, float] | None:
    """Combine the pillars, discounting sentiment by the news behind it.

    Returns the composite and the total weight it was built from — the latter
    is `coverage`, and it now means what it says: the share of the intended
    inputs that actually contributed. A symbol scored on price alone reports
    0.6; one with a single article reports 0.68, not 1.0.

    Shared by the live score and the validation harness on purpose. If the
    backtest blended differently from the endpoint, it would be measuring a
    ranking nobody is served.
    """
    weights: dict[str, float] = {}
    values: dict[str, float] = {}

    if technical is not None:
        weights["technical"] = PILLAR_WEIGHTS["technical"]
        values["technical"] = technical
    if sentiment is not None:
        confidence = sentiment_confidence(news_count)
        if confidence:
            weights["sentiment"] = PILLAR_WEIGHTS["sentiment"] * confidence
            values["sentiment"] = sentiment
    # Absent for any symbol the vendor does not cover, which is most non-US
    # listings. Missing means excluded and the rest reweighted, exactly as a
    # missing sentiment pillar behaves — `coverage` reports how much of the
    # intended input the score was actually built from.
    if fundamental is not None:
        weights["fundamental"] = PILLAR_WEIGHTS["fundamental"]
        values["fundamental"] = fundamental

    total = sum(weights.values())
    if not total:
        return None
    return sum(weights[name] * value for name, value in values.items()) / total, total


@dataclass
class _Raw:
    """Per-symbol inputs, before anything is ranked against anything else."""

    stock: Stock
    technicals: Technicals
    sentiment: dict[str, float | None]
    news_count: int
    fundamentals: dict[str, float | None] = field(default_factory=dict)


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

    # Reported per symbol but not blended — see FUNDAMENTAL_WEIGHTS.
    from app.services import fundamentals as fundamentals_service

    factors = await fundamentals_service.load_all(db)

    return [
        _Raw(
            stock=stock,
            technicals=technicals.compute(series.get(stock.id, [])),
            sentiment=_summarise_news(news.get(stock.id, []), days),
            news_count=len(news.get(stock.id, [])),
            fundamentals=_fundamental_values(factors.get(stock.id)),
        )
        for stock in stocks
    ]


def _fundamental_values(factor) -> dict[str, float | None]:
    """The stored fundamentals, keyed to match FUNDAMENTAL_WEIGHTS."""
    if factor is None:
        return {key: None for key in FUNDAMENTAL_WEIGHTS}
    return {
        "earnings_surprise_pct": factor.earnings_surprise_pct,
        "analyst_revision": factor.analyst_revision,
    }


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
) -> tuple[float | None, list[Factor], float]:
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
        return None, factors, 0.0
    # The third value is the share of the pillar's intended weight that was
    # actually available. Reported separately per pillar because the two run
    # short for unrelated reasons: a technical pillar is incomplete when the
    # symbol lacks price history, a sentiment one when nobody wrote about it.
    # Collapsing them into one number made "not enough history" and "quiet
    # week" indistinguishable, and the second is the normal case.
    return round(weighted / total_weight, 2), factors, round(total_weight, 4)


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

    fundamental_values = {
        key: {raw.stock.ticker: raw.fundamentals.get(key) for raw in inputs}
        for key in FUNDAMENTAL_WEIGHTS
    }

    technical_ranks = {key: percentile_ranks(values) for key, values in technical_values.items()}
    sentiment_ranks = {key: percentile_ranks(values) for key, values in sentiment_values.items()}
    fundamental_ranks = {
        key: percentile_ranks(values) for key, values in fundamental_values.items()
    }

    scored: list[StockScore] = []
    for raw in inputs:
        technical, technical_factors, technical_weight = _pillar(
            raw,
            technical_ranks,
            TECHNICAL_WEIGHTS,
            {key: getattr(raw.technicals, key) for key in TECHNICAL_WEIGHTS},
        )
        sentiment, sentiment_factors, _ = _pillar(
            raw, sentiment_ranks, SENTIMENT_WEIGHTS, raw.sentiment
        )
        fundamental, fundamental_factors, _ = _pillar(
            raw, fundamental_ranks, FUNDAMENTAL_WEIGHTS, raw.fundamentals
        )

        blended = _blend(technical, sentiment, raw.news_count, fundamental)
        if blended is None:
            continue
        composite, weight_total = blended

        scored.append(
            StockScore(
                ticker=raw.stock.ticker,
                company_name=raw.stock.company_name,
                sector_group=sectors.group_for(raw.stock.sector),
                sector=raw.stock.sector,
                score=round(composite, 2),
                technical_score=technical,
                sentiment_score=sentiment,
                sentiment_confidence=round(sentiment_confidence(raw.news_count), 2),
                technical_coverage=technical_weight,
                fundamental_score=fundamental,
                fundamental_factors=fundamental_factors,
                # What share of the intended inputs this score actually used.
                coverage=round(weight_total, 2),
                factors=sorted(
                    technical_factors + sentiment_factors + fundamental_factors,
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


# The ranking is split into quintiles, so five buckets.
BUCKETS = 5

# A period counts as well dispersed when the factor takes at least this many
# distinct values. Below twice the bucket count, which member of a tied block
# lands in the top bucket is decided by sort order rather than by the factor,
# and the measured spread is a sample of a tie rather than a reading of the
# ranking. Such periods are still reported — they are not wrong, just
# uninformative — but they are summarised separately, because a factor whose
# headline comes only from its most degenerate periods has not been shown to
# work, and the overall mean cannot show that on its own.
WELL_DISPERSED_MIN_DISTINCT = BUCKETS * 2


def _stdev(values: list[float]) -> float | None:
    """Sample standard deviation, or None when one period cannot have one."""
    if len(values) < 2:
        return None
    mean = sum(values) / len(values)
    variance = sum((value - mean) ** 2 for value in values) / (len(values) - 1)
    return round(variance**0.5, 4)


def _spread(ranked: list[tuple[str, float]], forward: dict[str, float]) -> dict:
    """Quintile the ranking and report top-minus-bottom forward return.

    Always returns a dict. When the spread cannot be computed it says which of
    the three reasons applied, because "we could not measure this pillar here"
    and "we measured it and it was flat" are different findings and a bare
    null reported them identically.
    """
    if len(ranked) < 10:
        return {"spread": None, "reason": "too_few_symbols", "symbols": len(ranked)}

    # Sort before testing for dispersion. This compared ranked[0] against
    # ranked[-1] on the *unsorted* list, which is two arbitrary symbols rather
    # than the range: whenever the first and last symbol in iteration order
    # happened to tie — common once heavy ties exist, as they do in a sentiment
    # factor where most articles score exactly zero — a perfectly rankable
    # period was discarded as flat. It suppressed three of six sentiment
    # periods here, and the pillar was judged on the half that survived.
    ordered = sorted(ranked, key=lambda row: row[1], reverse=True)
    if ordered[0][1] == ordered[-1][1]:
        # Now genuinely every symbol scored the same: the order is arbitrary
        # and any spread would be an artefact of the sort, not a finding.
        return {"spread": None, "reason": "no_dispersion", "symbols": len(ranked)}

    size = max(1, len(ordered) // BUCKETS)
    top = [forward[symbol] for symbol, _ in ordered[:size] if symbol in forward]
    bottom = [forward[symbol] for symbol, _ in ordered[-size:] if symbol in forward]
    if not top or not bottom:
        return {
            "spread": None,
            "reason": "no_forward_returns",
            "symbols": len(ordered),
        }

    top_mean = sum(top) / len(top)
    bottom_mean = sum(bottom) / len(bottom)
    return {
        "top_mean": round(top_mean, 4),
        "bottom_mean": round(bottom_mean, 4),
        "spread": round(top_mean - bottom_mean, 4),
        "symbols": len(ordered),
        # "Not all tied" is a weak thing to know. A factor with six distinct
        # values across 150 symbols is ranking them into six blocks, and which
        # members of a block land in the top quintile is still sort order — so
        # report the count rather than leaving a pass/fail flag to imply the
        # buckets were cleanly separated.
        "distinct_scores": len({value for _, value in ordered}),
    }


def _rank_at(
    as_of: datetime,
    history: dict[str, list[tuple[datetime, float]]],
    news: dict[str, list[tuple[datetime, float, str]]],
    news_days: int,
    earnings: dict[str, list[tuple[datetime, float]]] | None = None,
) -> dict[str, list[tuple[str, float]]]:
    """Rank the universe as it stood at ``as_of``, three ways.

    Returns one ranking per strategy so the pillars can be judged separately.
    A blended score that works only because one half carries it is worth
    knowing about — and so is a half that actively subtracts.
    """
    tech_inputs: dict[str, Technicals] = {}
    sent_inputs: dict[str, dict[str, float | None]] = {}
    sent_counts: dict[str, int] = {}

    for symbol, series in history.items():
        past = [row for row in series if row[0] <= as_of]
        if len(past) < MIN_SESSIONS:
            continue
        tech_inputs[symbol] = technicals.compute(past)

        window_start = as_of - timedelta(days=news_days)
        rows = [
            row for row in news.get(symbol, []) if window_start <= row[0] <= as_of
        ]
        sent_inputs[symbol] = _summarise_news_at(rows, as_of)
        sent_counts[symbol] = len(rows)

    if not tech_inputs:
        return {"technical": [], "sentiment": [], "blended": []}

    tech_ranks = {
        key: percentile_ranks({s: getattr(t, key) for s, t in tech_inputs.items()})
        for key in TECHNICAL_WEIGHTS
    }
    sent_ranks = {
        key: percentile_ranks({s: v.get(key) for s, v in sent_inputs.items()})
        for key in SENTIMENT_WEIGHTS
    }

    # Earnings surprise as it stood at as_of: the most recent quarter reported
    # *before* that date. Using the latest stored figure instead would leak a
    # number that had not been published yet, which is the classic way a
    # backtest flatters a factor it should be testing.
    surprises: dict[str, float] = {}
    for symbol in tech_inputs:
        past = [row for row in (earnings or {}).get(symbol, []) if row[0] <= as_of]
        if past:
            surprises[symbol] = max(past, key=lambda row: row[0])[1]
    surprise_ranks = percentile_ranks(
        {symbol: surprises.get(symbol) for symbol in tech_inputs}
    )

    rankings: dict[str, list[tuple[str, float]]] = {
        "technical": [],
        "sentiment": [],
        "fundamental": [],
        "blended": [],
    }

    for symbol in tech_inputs:
        technical = _weighted(symbol, tech_ranks, TECHNICAL_WEIGHTS)
        sentiment = _weighted(symbol, sent_ranks, SENTIMENT_WEIGHTS)

        if technical is not None:
            rankings["technical"].append((symbol, technical))
        if sentiment is not None:
            rankings["sentiment"].append((symbol, sentiment))
        # Ranked on its own as well as inside the blend, so the pillar can
        # still be judged separately from the mixture it now contributes to.
        fundamental = surprise_ranks.get(symbol)
        if fundamental is not None:
            rankings["fundamental"].append((symbol, fundamental))

        blended = _blend(
            technical, sentiment, sent_counts.get(symbol, 0), fundamental
        )
        if blended is not None:
            rankings["blended"].append((symbol, blended[0]))

    return rankings


def _weighted(
    symbol: str,
    ranks: dict[str, dict[str, float | None]],
    weights: dict[str, tuple[str, float, str]],
) -> float | None:
    """Weighted mean of the factors available for one symbol."""
    total = 0.0
    weighted = 0.0
    for key, (_, weight, _) in weights.items():
        percentile = ranks[key].get(symbol)
        if percentile is None:
            continue
        effective = 100 - percentile if key in _INVERTED else percentile
        total += weight
        weighted += effective * weight
    return weighted / total if total else None


def _summarise_news_at(
    rows: list[tuple[datetime, float, str]], as_of: datetime
) -> dict[str, float | None]:
    """The sentiment factors, computed as they would have looked at ``as_of``."""
    if not rows:
        return {key: None for key in SENTIMENT_WEIGHTS}

    recent_cutoff = as_of - timedelta(days=7)
    scores = [score for _, score, _ in rows]
    recent = [score for published, score, _ in rows if published >= recent_cutoff]
    older = [score for published, score, _ in rows if published < recent_cutoff]

    trend = None
    if recent and older:
        trend = sum(recent) / len(recent) - sum(older) / len(older)

    positives = sum(1 for _, _, sentiment in rows if sentiment == "positive")
    return {
        "sentiment_mean_30d": sum(scores) / len(scores),
        "sentiment_trend": trend,
        "news_volume_30d": _log_volume(len(rows)),
        "positive_share_30d": positives / len(rows) * 100,
    }


async def validate(
    db: AsyncSession,
    as_of_days_ago: int = 30,
    horizon_days: int = 21,
    periods: int = 6,
    step_days: int = 21,
) -> dict:
    """Did a high score precede a better return — repeatedly, and which pillar?

    Two things a single-period test cannot do, and both matter:

    * **Several start dates.** One month can flatter or damn any ranking. A
      momentum score in a reversal month looks catastrophic and tells you
      nothing about the score. Reporting the spread per period, and how many
      periods were positive, separates "this does not work" from "that month
      went against it".
    * **Each pillar separately.** A blended score that works only because one
      half carries it is worth knowing about, and a half that actively
      subtracts is worth knowing about urgently. Technical-only,
      sentiment-only and blended are ranked and measured independently.

    Every ranking uses only data timestamped at or before its ``as_of``.
    """
    now = datetime.now(timezone.utc)

    stocks = list(
        (
            await db.execute(
                select(Stock).where(Stock.is_active.is_(True)).order_by(Stock.ticker)
            )
        ).scalars()
    )
    by_id = {stock.id: stock.ticker for stock in stocks}
    if not by_id:
        return {"status": "no_stocks", "periods": []}

    price_rows = (
        await db.execute(
            select(StockPrice.ticker_id, StockPrice.price_date, StockPrice.close).where(
                StockPrice.ticker_id.in_(by_id)
            )
        )
    ).all()
    history: dict[str, list[tuple[datetime, float]]] = {}
    for ticker_id, price_date, close in price_rows:
        if close is None:
            continue
        moment = price_date if price_date.tzinfo else price_date.replace(tzinfo=timezone.utc)
        history.setdefault(by_id[ticker_id], []).append((moment, close))
    for series in history.values():
        series.sort(key=lambda row: row[0])

    news_rows = (
        await db.execute(
            select(NewsArticle.ticker_id, NewsArticle.published_at, SentimentScore.score,
                   SentimentScore.sentiment)
            .join(SentimentScore, SentimentScore.article_id == NewsArticle.id)
            .where(
                NewsArticle.ticker_id.in_(by_id),
                NewsArticle.duplicate_of_id.is_(None),
            )
        )
    ).all()
    news: dict[str, list[tuple[datetime, float, str]]] = {}
    for ticker_id, published_at, score, sentiment in news_rows:
        moment = (
            published_at if published_at.tzinfo else published_at.replace(tzinfo=timezone.utc)
        )
        news.setdefault(by_id[ticker_id], []).append((moment, score or 0.0, sentiment))

    # Reported quarters, for the fundamental strategy. Keyed by the period the
    # figures describe rather than when the vendor row was written, so "what
    # was known at as_of" means what had actually been reported by then.
    earnings: dict[str, list[tuple[datetime, float]]] = {}
    for ticker_id, period, surprise in (
        await db.execute(
            select(
                EarningsReport.ticker_id,
                EarningsReport.period,
                EarningsReport.eps_surprise_pct,
            ).where(
                EarningsReport.ticker_id.in_(by_id),
                EarningsReport.eps_surprise_pct.is_not(None),
            )
        )
    ).all():
        moment = period if period.tzinfo else period.replace(tzinfo=timezone.utc)
        earnings.setdefault(by_id[ticker_id], []).append((moment, surprise))

    results: list[dict] = []
    for index in range(periods):
        as_of = now - timedelta(days=as_of_days_ago + index * step_days)
        horizon_end = as_of + timedelta(days=horizon_days)

        forward: dict[str, float] = {}
        for symbol, series in history.items():
            past = [row for row in series if row[0] <= as_of]
            after = [row for row in series if as_of < row[0] <= horizon_end]
            if not past or not after or not past[-1][1]:
                continue
            forward[symbol] = round((after[-1][1] - past[-1][1]) / past[-1][1] * 100, 4)

        if len(forward) < 10:
            continue

        rankings = _rank_at(as_of, history, news, news_days=30, earnings=earnings)
        period = {"as_of": as_of.date().isoformat(), "symbols": len(forward)}
        for strategy, ranked in rankings.items():
            period[strategy] = _spread(ranked, forward)
        if any(period[name]["spread"] is not None for name in rankings):
            results.append(period)

    if not results:
        return {
            "status": "insufficient_history",
            "detail": (
                "Not enough price history to score a past date and measure what "
                "followed. Load more with "
                "POST /admin/ingest/yahoo?range=2y&only_missing=false."
            ),
            "periods": [],
        }

    summary: dict[str, dict] = {}
    for strategy in ("technical", "sentiment", "fundamental", "blended"):
        spreads = [
            period[strategy]["spread"]
            for period in results
            if period[strategy]["spread"] is not None
        ]
        unmeasured: dict[str, int] = {}
        for period in results:
            reason = period[strategy].get("reason")
            if period[strategy]["spread"] is None and reason:
                unmeasured[reason] = unmeasured.get(reason, 0) + 1

        # Restricted to periods where the factor actually separated symbols.
        dispersed = [
            period[strategy]["spread"]
            for period in results
            if period[strategy]["spread"] is not None
            and period[strategy].get("distinct_scores", 0) >= WELL_DISPERSED_MIN_DISTINCT
        ]

        # How many symbols this strategy actually ranked, averaged over the
        # periods it ranked any. Not decoration: the strategies do not rank the
        # same universe. Technical needs price history, which nearly everything
        # has; fundamental needs a reported earnings surprise, which only the
        # vendor-covered US listings have. Comparing a mean spread over ~100
        # large US names against one over ~220 symbols spanning four continents
        # and as many currencies is not comparing two factors — the smaller,
        # more homogeneous sample has less dispersion in forward returns for
        # reasons that have nothing to do with whether the factor works. The
        # count belongs beside the number it qualifies.
        ranked_counts = [
            period[strategy]["symbols"]
            for period in results
            if period[strategy].get("symbols")
        ]
        entry: dict = {"periods": len(spreads)}
        if ranked_counts:
            entry["mean_symbols_ranked"] = round(
                sum(ranked_counts) / len(ranked_counts), 1
            )
        if spreads:
            entry.update(
                {
                    "mean_spread": round(sum(spreads) / len(spreads), 4),
                    "median_spread": round(_median(spreads) or 0.0, 4),
                    # The honest headline: how often did the top half of the
                    # ranking actually beat the bottom? Five of six is a
                    # signal; three of six is a coin toss with extra steps.
                    "periods_positive": sum(1 for value in spreads if value > 0),
                    # Spread across periods, so a large mean built out of wild
                    # swings cannot pass for a stable one. With this few
                    # periods it is context, not a significance test.
                    "spread_stdev": _stdev(spreads),
                }
            )
        # The same three numbers over the periods that could actually rank.
        # Where this diverges sharply from the headline, the headline is
        # measuring ties.
        if spreads:
            entry["well_dispersed"] = {
                "periods": len(dispersed),
                "min_distinct_scores": WELL_DISPERSED_MIN_DISTINCT,
                "mean_spread": (
                    round(sum(dispersed) / len(dispersed), 4) if dispersed else None
                ),
                "periods_positive": sum(1 for value in dispersed if value > 0),
            }
        # Why the other periods could not be measured, rather than a silent gap
        # in the period list that reads as an absent result.
        if unmeasured:
            entry["periods_unmeasured"] = unmeasured
        summary[strategy] = entry

    return {
        "status": "ok",
        "horizon_days": horizon_days,
        "periods_tested": len(results),
        "step_days": step_days,
        "summary": summary,
        "periods": results,
        "caveat": (
            ("Overlapping windows, " if step_days < horizon_days else "")
            + "one universe, no transaction costs, no survivorship control, "
            "and the periods share market conditions rather than being "
            "independent draws. With this many periods the standard error on "
            "any mean spread is wide enough to contain zero unless the effect "
            "is very large. Evidence, not proof."
        ),
    }
