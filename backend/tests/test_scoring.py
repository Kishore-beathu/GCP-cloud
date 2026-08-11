"""The ranked score: its arithmetic, its honesty, and its validation."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.models import NewsArticle, SentimentScore, Stock, StockPrice
from app.services import scoring


async def add_prices(db, stock: Stock, closes: list[float], end: datetime | None = None):
    """Give a stock a daily closing series ending today."""
    end = end or datetime.now(timezone.utc)
    for offset, close in enumerate(reversed(closes)):
        db.add(
            StockPrice(
                ticker_id=stock.id,
                close=close,
                price_date=end - timedelta(days=offset),
                source="test",
            )
        )
    await db.commit()


async def add_news(db, stock: Stock, scores: list[float], days_ago: int = 1):
    for index, score in enumerate(scores):
        article = NewsArticle(
            ticker_id=stock.id,
            headline=f"Story {index} about {stock.ticker}",
            source="test",
            url=f"https://example.com/{stock.ticker}/{index}",
            published_at=datetime.now(timezone.utc) - timedelta(days=days_ago),
        )
        db.add(article)
        db.add(
            SentimentScore(
                article=article,
                sentiment="positive" if score > 0 else "negative",
                score=score,
                confidence=0.8,
                event_type="other",
                event_confidence=0.5,
                model_version="test",
            )
        )
    await db.commit()


# --- Percentiles -------------------------------------------------------------


def test_percentiles_span_the_full_range():
    ranks = scoring.percentile_ranks({"a": 1.0, "b": 2.0, "c": 3.0})

    assert ranks["a"] == 0.0
    assert ranks["c"] == 100.0
    assert ranks["b"] == 50.0


def test_ties_share_the_midpoint():
    """Otherwise sort order hands one of several equal values an advantage."""
    ranks = scoring.percentile_ranks({"a": 5.0, "b": 5.0, "c": 5.0})

    assert ranks["a"] == ranks["b"] == ranks["c"]


def test_missing_values_stay_missing():
    """An absent factor must not be scored as if it were the worst."""
    ranks = scoring.percentile_ranks({"a": 1.0, "b": None})

    assert ranks["b"] is None
    assert ranks["a"] is not None


def test_a_single_value_is_the_midpoint():
    assert scoring.percentile_ranks({"only": 7.0})["only"] == 50.0


def test_all_missing_is_handled():
    assert scoring.percentile_ranks({"a": None, "b": None}) == {"a": None, "b": None}


# --- Ranking -----------------------------------------------------------------


@pytest.mark.asyncio
async def test_stronger_momentum_ranks_higher(db, seeded_stocks):
    """The whole point: one call answers "which of these looks best"."""
    riser, faller = seeded_stocks
    await add_prices(db, riser, [100.0 + index for index in range(80)])
    await add_prices(db, faller, [200.0 - index for index in range(80)])

    scored = await scoring.score_universe(db)

    assert [item.ticker for item in scored] == [riser.ticker, faller.ticker]
    assert scored[0].rank == 1
    assert scored[0].universe_size == 2


@pytest.mark.asyncio
async def test_every_score_carries_its_own_arithmetic(db, seeded_stocks):
    """Explainability is the point of the design, not a debugging aid."""
    stock = seeded_stocks[0]
    await add_prices(db, stock, [100.0 + index for index in range(80)])

    item = (await scoring.score_universe(db))[0]

    assert item.factors, "a score with no factors cannot be audited"
    for factor in item.factors:
        assert factor.label
        assert factor.explanation
        assert 0 <= factor.percentile <= 100
        # The contribution is exactly percentile x weight, checkable by eye.
        assert factor.contribution == pytest.approx(factor.percentile * factor.weight, abs=0.02)


@pytest.mark.asyncio
async def test_lower_volatility_scores_higher(db, seeded_stocks):
    """The one inverted factor; a sign error here would be invisible."""
    calm, wild = seeded_stocks
    await add_prices(db, calm, [100.0 + index * 0.1 for index in range(80)])
    await add_prices(
        db, wild, [100.0 + index * 0.1 + (8.0 if index % 2 else -8.0) for index in range(80)]
    )

    scored = {item.ticker: item for item in await scoring.score_universe(db)}
    calm_factor = next(f for f in scored[calm.ticker].factors if f.key == "volatility_21d")
    wild_factor = next(f for f in scored[wild.ticker].factors if f.key == "volatility_21d")

    assert calm_factor.value < wild_factor.value      # calmer in raw terms
    assert calm_factor.percentile > wild_factor.percentile  # and scored better


@pytest.mark.asyncio
async def test_a_symbol_with_only_news_is_still_scored(db, seeded_stocks):
    """Coverage reports how much was known rather than dropping the symbol."""
    stock = seeded_stocks[0]
    await add_news(db, stock, [0.8, 0.6, 0.7])

    scored = await scoring.score_universe(db)
    item = next(entry for entry in scored if entry.ticker == stock.ticker)

    assert item.technical_score is None
    assert item.sentiment_score is not None
    assert item.news_count_30d == 3
    # Three articles earn three fifths of the pillar's weight, and coverage
    # says so rather than claiming the score rests on a full input.
    assert item.sentiment_confidence == 0.6
    assert item.coverage == round(scoring.PILLAR_WEIGHTS["sentiment"] * 0.6, 2)


@pytest.mark.asyncio
async def test_one_article_cannot_carry_the_full_sentiment_weight(db, seeded_stocks):
    """A single story is an anecdote; it must not move 40% of a symbol's rank."""
    thin, thick = seeded_stocks
    for stock in (thin, thick):
        await add_prices(db, stock, [100.0 * 1.002**day for day in range(80)])
    await add_news(db, thin, [0.9])
    await add_news(db, thick, [0.9] * 6)

    scored = {item.ticker: item for item in await scoring.score_universe(db)}

    assert scored[thin.ticker].sentiment_confidence == 0.2
    assert scored[thick.ticker].sentiment_confidence == 1.0
    # The thinly covered symbol leans on its price history instead.
    assert scored[thin.ticker].coverage < scored[thick.ticker].coverage


@pytest.mark.asyncio
async def test_a_symbol_with_no_data_is_not_ranked(db, seeded_stocks):
    """Ranking a symbol nothing is known about would be inventing a result."""
    assert await scoring.score_universe(db) == []


@pytest.mark.asyncio
async def test_short_history_does_not_produce_a_technical_score(db, seeded_stocks):
    """Momentum from five sessions is noise wearing a number's clothes."""
    stock = seeded_stocks[0]
    await add_prices(db, stock, [100.0, 101.0, 102.0, 103.0, 104.0])
    await add_news(db, stock, [0.5])

    item = next(
        entry for entry in await scoring.score_universe(db) if entry.ticker == stock.ticker
    )

    assert item.technical_score is None


@pytest.mark.asyncio
async def test_duplicate_articles_do_not_inflate_the_sentiment_pillar(db, seeded_stocks):
    """Four wires carrying one release is one event, not four."""
    stock = seeded_stocks[0]
    await add_news(db, stock, [0.9])

    primary = (await db.execute(__import__("sqlalchemy").select(NewsArticle))).scalars().first()
    copy = NewsArticle(
        ticker_id=stock.id,
        headline="Syndicated copy",
        source="other_wire",
        url="https://example.com/copy",
        published_at=datetime.now(timezone.utc),
        duplicate_of_id=primary.id,
    )
    db.add(copy)
    await db.commit()

    item = next(
        entry for entry in await scoring.score_universe(db) if entry.ticker == stock.ticker
    )

    assert item.news_count_30d == 1


@pytest.mark.asyncio
async def test_sector_rank_is_separate_from_overall_rank(db, seeded_stocks):
    """"Best storage name" is a different question from "best name"."""
    first, second = seeded_stocks
    second.sector = "storage_hardware"
    await db.commit()
    await add_prices(db, first, [100.0 + index for index in range(80)])
    await add_prices(db, second, [100.0 + index * 0.5 for index in range(80)])

    scored = {item.ticker: item for item in await scoring.score_universe(db)}

    # Second overall, but alone and therefore first in its own group.
    assert scored[second.ticker].rank == 2
    assert scored[second.ticker].sector_rank == 1
    assert scored[second.ticker].sector_size == 1


# --- Validation --------------------------------------------------------------


@pytest.mark.asyncio
async def test_validation_says_so_when_there_is_not_enough_history(db, seeded_stocks):
    """"Insufficient data" is a real answer and must not read as a failure."""
    result = await scoring.validate(db)

    assert result["status"] == "insufficient_history"
    assert "admin/ingest/yahoo" in result["detail"]


@pytest.mark.asyncio
async def test_validation_measures_each_pillar_over_several_periods(db):
    """One month can flatter or damn any ranking; several separate the two."""
    stocks = [
        Stock(ticker=f"T{index:02d}", company_name=f"Test {index}", sector="pharma")
        for index in range(20)
    ]
    db.add_all(stocks)
    await db.commit()
    for stock in stocks:
        await db.refresh(stock)

    now = datetime.now(timezone.utc)
    for index, stock in enumerate(stocks):
        # Compounding, not linear: a linear downtrend over 220 sessions drives
        # the price through zero and then negative, which makes every
        # percentage return meaningless. Real prices compound.
        rate = 1.004 if index < 10 else 0.996
        await add_prices(db, stock, [100.0 * rate**day for day in range(220)], end=now)

    result = await scoring.validate(db, as_of_days_ago=25, horizon_days=15, periods=3, step_days=20)

    assert result["status"] == "ok"
    assert result["periods_tested"] >= 2
    # The technical pillar is measured on its own, not only inside the blend.
    assert "technical" in result["summary"]
    technical = result["summary"]["technical"]
    assert technical["periods"] >= 2
    assert technical["periods_positive"] == technical["periods"]
    assert technical["mean_spread"] > 0


@pytest.mark.asyncio
async def test_validation_reports_periods_individually(db):
    """The per-period detail is what shows a single bad month for what it is."""
    stocks = [
        Stock(ticker=f"P{index:02d}", company_name=f"Per {index}", sector="pharma")
        for index in range(15)
    ]
    db.add_all(stocks)
    await db.commit()
    for stock in stocks:
        await db.refresh(stock)

    now = datetime.now(timezone.utc)
    for index, stock in enumerate(stocks):
        rate = 1.004 if index < 8 else 0.998
        await add_prices(db, stock, [100.0 * rate**day for day in range(200)], end=now)

    result = await scoring.validate(db, as_of_days_ago=25, horizon_days=15, periods=3, step_days=20)

    assert result["periods"]
    for period in result["periods"]:
        assert "as_of" in period
        assert period["symbols"] > 0


@pytest.mark.asyncio
async def test_validation_refuses_to_rank_when_nothing_separates(db):
    """Flat history means every symbol ties, so any spread is sort order.

    This was found by a look-ahead test: with identical scores the buckets are
    arbitrary, and the machinery happily reported a 50-point spread that was
    purely an artefact of insertion order. A confident number from no signal is
    the worst output a validation can produce.
    """
    stocks = [
        Stock(ticker=f"L{index:02d}", company_name=f"Look {index}", sector="pharma")
        for index in range(12)
    ]
    db.add_all(stocks)
    await db.commit()
    for stock in stocks:
        await db.refresh(stock)

    now = datetime.now(timezone.utc)
    for index, stock in enumerate(stocks):
        # Flat until the cut-off, then a violent divergence. A score that saw
        # the future would track that divergence exactly.
        await add_prices(db, stock, [100.0] * 60 + [100.0 + (index - 6) * 5.0] * 25, end=now)

    result = await scoring.validate(db, as_of_days_ago=25, horizon_days=20, periods=1)

    # No dispersion to rank on, so no spread is reported at all.
    assert result["status"] == "insufficient_history" or not result.get("summary")


def test_an_unmeasurable_pillar_says_why_rather_than_returning_null():
    """"Could not measure" and "measured, found nothing" are different results.

    A bare null reported both identically, so a sentiment pillar absent for
    three of six periods gave no way to tell whether there was no news, too few
    symbols, or no dispersion among the ones there were.
    """
    forward = {f"S{index:02d}": float(index) for index in range(12)}

    too_few = scoring._spread([("S00", 1.0), ("S01", 2.0)], forward)
    assert too_few["spread"] is None
    assert too_few["reason"] == "too_few_symbols"

    tied = scoring._spread([(symbol, 50.0) for symbol in forward], forward)
    assert tied["spread"] is None
    assert tied["reason"] == "no_dispersion"

    unpriced = scoring._spread(
        [(f"X{index:02d}", float(index)) for index in range(12)], forward
    )
    assert unpriced["spread"] is None
    assert unpriced["reason"] == "no_forward_returns"

    measured = scoring._spread(
        [(symbol, float(symbol[1:])) for symbol in forward], forward
    )
    assert measured["spread"] is not None
    assert "reason" not in measured


@pytest.mark.asyncio
async def test_validation_states_its_limits(db):
    """A number without its caveat gets quoted without its caveat."""
    result = await scoring.validate(db)

    assert "caveat" in result or result["status"] != "ok"


# --- API ---------------------------------------------------------------------


@pytest.mark.asyncio
async def test_scores_endpoint_ranks_and_explains(client, db, seeded_stocks):
    riser, faller = seeded_stocks
    await add_prices(db, riser, [100.0 + index for index in range(80)])
    await add_prices(db, faller, [200.0 - index for index in range(80)])

    body = (await client.get("/scores")).json()

    assert body["scores"][0]["ticker"] == riser.ticker
    assert body["scores"][0]["rank"] == 1
    assert body["scores"][0]["factors"]
    # The weights used are published with the result, not hidden in the code.
    assert body["weights"]["pillars"]["technical"] > 0
    assert "does not forecast" in body["method"]


@pytest.mark.asyncio
async def test_scores_endpoint_filters_by_group_without_re_ranking(client, db, seeded_stocks):
    """Rank must mean the same thing in every response."""
    first, second = seeded_stocks
    second.sector = "storage_hardware"
    await db.commit()
    await add_prices(db, first, [100.0 + index for index in range(80)])
    await add_prices(db, second, [100.0 + index * 0.5 for index in range(80)])

    body = (await client.get("/scores?group=data_storage")).json()

    assert [item["ticker"] for item in body["scores"]] == [second.ticker]
    # Still second overall, even though it is the only row returned.
    assert body["scores"][0]["rank"] == 2


@pytest.mark.asyncio
async def test_scores_endpoint_rejects_an_unknown_group(client, seeded_stocks):
    assert (await client.get("/scores?group=nonsense")).status_code == 422


@pytest.mark.asyncio
async def test_single_score_endpoint_returns_the_factor_breakdown(client, db, seeded_stocks):
    stock = seeded_stocks[0]
    await add_prices(db, stock, [100.0 + index for index in range(80)])

    body = (await client.get(f"/scores/{stock.ticker}")).json()

    assert body["ticker"] == stock.ticker
    assert any(factor["key"] == "momentum_63d" for factor in body["factors"])


@pytest.mark.asyncio
async def test_single_score_explains_a_404(client, seeded_stocks):
    """"Not found" should say which of the two reasons applies."""
    response = await client.get("/scores/MRNA")

    assert response.status_code == 404
    assert "price history" in response.json()["detail"]


@pytest.mark.asyncio
async def test_validation_endpoint_is_reachable(client, seeded_stocks):
    body = (await client.get("/scores/validation")).json()

    assert body["status"] in {"ok", "insufficient_history", "no_dispersion", "no_stocks"}
