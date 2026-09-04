"""The ranked score: its arithmetic, its honesty, and its validation."""

from __future__ import annotations

import math

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


def test_dispersion_is_judged_on_the_range_not_on_two_arbitrary_symbols():
    """Regression: the flatness check ran before the sort.

    It compared the first and last entries of the *unsorted* ranking, so any
    period whose first and last symbol in iteration order happened to tie was
    discarded as flat. Heavy ties make that likely rather than exotic — in a
    sentiment factor where most articles score exactly zero it suppressed
    three of six periods, and the pillar was then judged on the half that
    survived.
    """
    forward = {f"S{index:02d}": float(index) for index in range(12)}
    # First and last tie; everything between them is spread wide.
    ranked = [("S00", 50.0)] + [
        (f"S{index:02d}", float(index) * 10) for index in range(1, 11)
    ] + [("S11", 50.0)]

    result = scoring._spread(ranked, forward)

    assert result["spread"] is not None, "a rankable period was discarded as flat"
    assert result["distinct_scores"] > 1


def test_a_genuinely_flat_ranking_is_still_refused():
    """The guard the sort-order bug was hiding behind still has to work."""
    forward = {f"S{index:02d}": float(index) for index in range(12)}

    result = scoring._spread([(symbol, 50.0) for symbol in forward], forward)

    assert result["spread"] is None
    assert result["reason"] == "no_dispersion"


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
async def test_scores_endpoint_filters_to_one_sector_inside_a_group(
    client, db, seeded_stocks
):
    """"The clinical-stage names" is not a question a group heading can answer.

    Ranking cohorts are groups on purpose — eleven sectors would leave several
    with two or three members, and a percentile over three symbols is not a
    percentile. Filtering is the opposite: the finer label is the useful one,
    and without it a 50-name cohort is only reachable by reading past the
    large caps it shares a group with.
    """
    first, second = seeded_stocks
    second.sector = "clinical_stage"
    await db.commit()
    await add_prices(db, first, [100.0 + index for index in range(80)])
    await add_prices(db, second, [100.0 + index * 0.5 for index in range(80)])

    body = (await client.get("/scores?sector=clinical_stage")).json()

    assert [item["ticker"] for item in body["scores"]] == [second.ticker]
    # Filtered afterwards, like the group filter: rank still means rank.
    assert body["scores"][0]["rank"] == 2
    assert body["scores"][0]["sector"] == "clinical_stage"
    assert body["scores"][0]["sector_group"] == "pharma_life_sciences"


@pytest.mark.asyncio
async def test_scores_endpoint_rejects_an_unknown_sector(client, seeded_stocks):
    """An unmapped sector matches nothing, which reads as "none qualified"."""
    assert (await client.get("/scores?sector=clinical")).status_code == 422
    assert (await client.get("/scores?sector=nonsense")).status_code == 422


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


@pytest.mark.asyncio
async def test_technical_coverage_is_separate_from_news_coverage(db, seeded_stocks):
    """Two pillars run short for unrelated reasons; one number hid that.

    A quiet week and a symbol with no price history both showed as partial
    `coverage`, so a flag built on it fired on nearly every row — and the
    common cause was the harmless one.
    """
    short, long = seeded_stocks
    # Enough sessions to be scored, too few for the 52-week range factor.
    await add_prices(db, short, [100.0 * 1.002**day for day in range(40)])
    await add_prices(db, long, [100.0 * 1.002**day for day in range(300)])
    await add_news(db, short, [0.5] * 10)
    await add_news(db, long, [0.5] * 10)

    scored = {item.ticker: item for item in await scoring.score_universe(db)}

    # Both have ample news, so neither is short on the sentiment side.
    assert scored[short.ticker].sentiment_confidence == 1.0
    assert scored[long.ticker].sentiment_confidence == 1.0
    # Only the short-history symbol is missing a price factor.
    assert scored[short.ticker].technical_coverage < 1.0
    assert scored[long.ticker].technical_coverage == 1.0
    assert not any(f.key == "range_position_52w" for f in scored[short.ticker].factors)


@pytest.mark.asyncio
async def test_a_quiet_symbol_is_not_reported_as_missing_price_history(db, seeded_stocks):
    """The distinction that makes the flag worth showing at all."""
    stock = seeded_stocks[0]
    await add_prices(db, stock, [100.0 * 1.002**day for day in range(300)])
    await add_news(db, stock, [0.5])  # one article: thin news, full history

    item = next(
        entry for entry in await scoring.score_universe(db) if entry.ticker == stock.ticker
    )

    assert item.technical_coverage == 1.0
    assert item.sentiment_confidence == 0.2


def test_well_dispersed_periods_are_summarised_apart_from_tied_ones():
    """A factor can look good purely through its most degenerate periods.

    Found in real output: the sentiment pillar's positive mean came entirely
    from periods where seventeen symbols were sorted into two score blocks, so
    the top quintile was three names drawn arbitrarily from one tie. In the
    periods where the factor genuinely varied it was negative. The overall
    mean cannot show that, so it is reported both ways.
    """
    forward = {f"S{index:02d}": float(index) for index in range(20)}

    # Two blocks: the quintile split is a coin toss inside each tie.
    tied = [(symbol, 1.0 if int(symbol[1:]) < 10 else 0.0) for symbol in forward]
    # Every symbol distinct: the ranking means something.
    spread_out = [(symbol, float(symbol[1:])) for symbol in forward]

    assert scoring._spread(tied, forward)["distinct_scores"] == 2
    assert (
        scoring._spread(spread_out, forward)["distinct_scores"]
        >= scoring.WELL_DISPERSED_MIN_DISTINCT
    )


@pytest.mark.asyncio
async def test_validation_reports_the_dispersed_subset(db):
    """The summary carries both readings, not just the flattering one."""
    stocks = [
        Stock(ticker=f"D{index:02d}", company_name=f"Disp {index}", sector="pharma")
        for index in range(20)
    ]
    db.add_all(stocks)
    await db.commit()
    for stock in stocks:
        await db.refresh(stock)

    now = datetime.now(timezone.utc)
    for index, stock in enumerate(stocks):
        # A distinct rate per symbol. Two shared rates would give every symbol
        # in a group an identical series and therefore an identical score —
        # two distinct values across twenty symbols, which is the degenerate
        # case this test is meant to be the opposite of.
        rate = 0.996 + index * 0.0008
        await add_prices(db, stock, [100.0 * rate**day for day in range(220)], end=now)

    result = await scoring.validate(db, as_of_days_ago=25, horizon_days=15, periods=3, step_days=20)

    technical = result["summary"]["technical"]
    assert "well_dispersed" in technical
    assert technical["well_dispersed"]["min_distinct_scores"] == scoring.WELL_DISPERSED_MIN_DISTINCT
    # Every symbol scores differently here, so every measured period qualifies.
    assert technical["well_dispersed"]["periods"] == technical["periods"]
    assert technical["well_dispersed"]["mean_spread"] is not None


# --- Fundamentals: measured, not weighted ------------------------------------


@pytest.mark.asyncio
async def test_fundamentals_move_the_score_now_that_they_measured(db, seeded_stocks):
    """The other half of the discipline: a factor that measures well gets used.

    This shipped weightless on purpose — earnings surprise has the strongest
    published evidence here, and the sentiment pillar was given 0.4 on the same
    kind of reasoning and twelve periods later had not earned it. The
    measurement then came back: mean spread +3.21 over twelve periods, positive
    in eleven, standard deviation 2.61 against technical's 10.73. So it carries
    weight, and the assertion flips with it.

    What must not change is the claim `factors` makes. It is the list whose
    contributions sum to the score, so a weighted factor now has to be in it —
    for exactly the reason an unweighted one had to be kept out.
    """
    from app.models import EarningsReport

    beat, missed = seeded_stocks
    for stock in (beat, missed):
        await add_prices(db, stock, [100.0 * 1.002**day for day in range(80)])
    db.add_all(
        [
            EarningsReport(
                ticker_id=beat.id,
                period=datetime.now(timezone.utc) - timedelta(days=10),
                eps_surprise_pct=25.0,
            ),
            EarningsReport(
                ticker_id=missed.id,
                period=datetime.now(timezone.utc) - timedelta(days=10),
                eps_surprise_pct=-25.0,
            ),
        ]
    )
    await db.commit()

    scored = {item.ticker: item for item in await scoring.score_universe(db)}

    # Reported, and ranked against each other.
    assert scored[beat.ticker].fundamental_score > scored[missed.ticker].fundamental_score
    assert scored[beat.ticker].fundamental_factors

    # And now part of the score's arithmetic, which is what `factors` claims.
    assert any(
        factor.key in scoring.FUNDAMENTAL_WEIGHTS
        for factor in scored[beat.ticker].factors
    )
    assert scoring.PILLAR_WEIGHTS["fundamental"] > 0
    # The beat outranks the miss on otherwise identical price history, which is
    # the weight actually doing something rather than merely existing.
    assert scored[beat.ticker].score > scored[missed.ticker].score


@pytest.mark.asyncio
async def test_a_symbol_without_earnings_coverage_is_reweighted_not_penalised(
    db, seeded_stocks
):
    """Most non-US listings have no vendor coverage, and absent is not zero.

    Scored as a zero percentile they would sink to the bottom of every ranking
    on a fact about the data vendor rather than about the company. Missing
    means excluded and the remaining pillars reweighted — the same treatment a
    symbol with no news already gets — and `coverage` says how much of the
    intended input the score was built from.
    """
    from app.models import EarningsReport

    # Three symbols, because a percentile needs peers: one surprise on its own
    # ranks at the midpoint however bad it is, which would make this pass for
    # the wrong reason.
    beat = Stock(ticker="AAA", company_name="Beat", sector="pharma")
    missed = Stock(ticker="BBB", company_name="Missed", sector="pharma")
    uncovered = Stock(ticker="CCC", company_name="Uncovered", sector="pharma")
    db.add_all([beat, missed, uncovered])
    await db.commit()
    for stock in (beat, missed, uncovered):
        await db.refresh(stock)
        await add_prices(db, stock, [100.0 * 1.002**day for day in range(80)])

    now = datetime.now(timezone.utc)
    db.add_all(
        [
            EarningsReport(
                ticker_id=beat.id, period=now - timedelta(days=10), eps_surprise_pct=40.0
            ),
            EarningsReport(
                ticker_id=missed.id,
                period=now - timedelta(days=10),
                eps_surprise_pct=-40.0,
            ),
        ]
    )
    await db.commit()

    scored = {item.ticker: item for item in await scoring.score_universe(db)}
    covered = beat

    assert scored[uncovered.ticker].fundamental_score is None
    # Reported as built from less real input, not marked down for it.
    assert scored[uncovered.ticker].coverage < scored[covered.ticker].coverage
    # A bad surprise is what moves a score down; having no surprise is not.
    assert scored[uncovered.ticker].score > scored[missed.ticker].score


@pytest.mark.asyncio
async def test_an_uncovered_symbol_is_pulled_to_the_middle_like_everyone_else(db):
    """Absent coverage must not buy a symbol a more extreme rank.

    Excluding the pillar and reweighting the rest left an uncovered symbol
    holding its raw technical percentile while covered peers had theirs pulled
    toward the middle by a second factor. On the live universe that made
    uncovered symbols 30% of the names and 47% of the top thirty — an artefact
    of which listings the vendor covers, not of the companies.

    Imputing the midpoint gives every symbol the same three axes: an uncovered
    symbol and one whose fundamentals are exactly average now score alike.
    """
    from app.models import EarningsReport

    strong = Stock(ticker="DDD", company_name="Strong tech", sector="pharma")
    average = Stock(ticker="EEE", company_name="Average all round", sector="pharma")
    weak = Stock(ticker="FFF", company_name="Weak", sector="pharma")
    db.add_all([strong, average, weak])
    await db.commit()
    for stock in (strong, average, weak):
        await db.refresh(stock)

    # Identical price history, so only the fundamental axis can separate them.
    for stock in (strong, average, weak):
        await add_prices(db, stock, [100.0 * 1.002**day for day in range(80)])

    now = datetime.now(timezone.utc)
    db.add_all(
        [
            EarningsReport(
                ticker_id=average.id, period=now - timedelta(days=10), eps_surprise_pct=5.0
            ),
            EarningsReport(
                ticker_id=weak.id, period=now - timedelta(days=10), eps_surprise_pct=-30.0
            ),
        ]
    )
    await db.commit()

    scored = {item.ticker: item for item in await scoring.score_universe(db)}

    # The uncovered symbol is scored, flagged, and not credited with coverage
    # it does not have.
    assert scored["DDD"].fundamental_imputed is True
    assert scored["DDD"].fundamental_score is None
    assert scored["EEE"].fundamental_imputed is False
    # And it does not outrank a symbol whose measured fundamentals are better
    # than the midpoint purely by having none.
    assert scored["DDD"].score <= scored["EEE"].score
    assert scored["DDD"].score > scored["FFF"].score


@pytest.mark.asyncio
async def test_the_backtest_only_sees_quarters_reported_by_then(db):
    """Lookahead is how a backtest flatters the factor it is testing.

    Ranking on the *latest* stored surprise would hand every past date a
    number that had not been published yet — and earnings surprise would look
    superb for exactly the wrong reason.
    """
    from app.models import EarningsReport

    stocks = [
        Stock(ticker=f"E{index:02d}", company_name=f"Earn {index}", sector="pharma")
        for index in range(12)
    ]
    db.add_all(stocks)
    await db.commit()
    for stock in stocks:
        await db.refresh(stock)

    now = datetime.now(timezone.utc)
    for index, stock in enumerate(stocks):
        await add_prices(db, stock, [100.0 * (0.998 + index * 0.0004) ** day for day in range(220)], end=now)
        # Reported *yesterday*: far later than any as_of the validation uses.
        db.add(
            EarningsReport(
                ticker_id=stock.id,
                period=now - timedelta(days=1),
                eps_surprise_pct=float(index * 10),
            )
        )
    await db.commit()

    result = await scoring.validate(db, as_of_days_ago=60, horizon_days=15, periods=2, step_days=20)

    # Every surprise postdates every as_of, so the fundamental strategy has
    # nothing to rank and must report that rather than borrowing the future.
    fundamental = result["summary"].get("fundamental", {})
    assert fundamental.get("periods", 0) == 0


@pytest.mark.asyncio
async def test_validation_reports_how_many_symbols_each_strategy_ranked(db):
    """The strategies do not rank the same universe, and the summary hid it.

    Technical needs price history, which nearly everything has. Fundamental
    needs a reported earnings surprise, which only the vendor-covered US
    listings have — on a real run that was ~100 large US names against ~220
    symbols spanning four continents and as many currencies. Two mean spreads
    printed side by side read as two measurements of two factors; they are
    measurements over different samples, and the smaller, more homogeneous one
    has less dispersion in forward returns whether or not its factor works.
    """
    stocks = [
        Stock(ticker=f"N{index:02d}", company_name=f"Sample {index}", sector="pharma")
        for index in range(20)
    ]
    db.add_all(stocks)
    await db.commit()
    for stock in stocks:
        await db.refresh(stock)

    now = datetime.now(timezone.utc)
    for index, stock in enumerate(stocks):
        rate = 1.004 if index < 10 else 0.996
        await add_prices(db, stock, [100.0 * rate**day for day in range(220)], end=now)

    result = await scoring.validate(
        db, as_of_days_ago=25, horizon_days=15, periods=3, step_days=20
    )

    technical = result["summary"]["technical"]
    assert technical["mean_symbols_ranked"] == 20


# --- Per-factor validation and significance -----------------------------------


def test_significance_calls_noise_noise():
    """The whole point: a large mean built of wild swings is not a finding."""
    from app.services.scoring import _significance

    noisy = [12.0, -8.0, 11.0, -7.0, 9.0, -6.0, 10.0, -5.0, 8.0, -4.0, 11.0, -3.0]
    result = _significance(noisy)

    assert abs(result["t_stat"]) < scoring.SIGNIFICANT_T
    assert "Not distinguishable from zero" in result["verdict"]


def test_significance_names_an_inverted_factor_as_inverted():
    """A factor that works backwards is a finding, not a failure to measure.

    Reported as merely "negative" it reads as noise; the top of that ranking
    underperforming the bottom consistently is information worth acting on.
    """
    from app.services.scoring import _significance

    result = _significance([-10.0, -8.0, -12.0, -9.0, -11.0, -13.0])

    assert result["t_stat"] < -scoring.SIGNIFICANT_T
    assert "inverted" in result["verdict"]


def test_significance_refuses_to_judge_too_few_periods():
    from app.services.scoring import _significance

    result = _significance([3.0, 4.0])

    assert result["t_stat"] is None
    assert "Too few periods" in result["verdict"]


def test_every_named_factor_is_validated_separately(db):
    """A pillar average hides its terms.

    A technical pillar sitting inside its own noise looks identical whether all
    five factors are weak or one is strong and four are diluting it. The
    percentiles were already computed and folded away.
    """
    names = scoring.strategy_names()

    for pillar in ("technical", "sentiment", "fundamental", "blended"):
        assert pillar in names
    for key in scoring.TECHNICAL_WEIGHTS:
        assert f"{scoring.FACTOR_PREFIX}{key}" in names
    for key in scoring.SENTIMENT_WEIGHTS:
        assert f"{scoring.FACTOR_PREFIX}{key}" in names


@pytest.mark.asyncio
async def test_validation_reports_each_factor_and_its_verdict(db):
    stocks = [
        Stock(ticker=f"F{index:02d}", company_name=f"Factor {index}", sector="pharma")
        for index in range(20)
    ]
    db.add_all(stocks)
    await db.commit()
    for stock in stocks:
        await db.refresh(stock)

    now = datetime.now(timezone.utc)
    for index, stock in enumerate(stocks):
        rate = 1.004 if index < 10 else 0.996
        await add_prices(db, stock, [100.0 * rate**day for day in range(220)], end=now)

    summary = (
        await scoring.validate(
            db, as_of_days_ago=25, horizon_days=15, periods=3, step_days=20
        )
    )["summary"]

    momentum = summary[f"{scoring.FACTOR_PREFIX}momentum_63d"]
    assert momentum["periods"] >= 2
    assert momentum["mean_spread"] > 0
    # The verdict travels with every strategy, including the ones with too
    # little evidence to judge.
    assert "verdict" in summary["technical"]
    assert "verdict" in momentum


@pytest.mark.asyncio
async def test_an_inverted_factor_is_ranked_the_way_the_score_uses_it(db):
    """Volatility is scored so that less is better.

    Ranked raw, the validation would measure the opposite of the factor the
    score actually applies, and report that the pillar's own input works
    backwards.
    """
    assert "volatility_21d" in scoring._INVERTED

    stocks = [
        Stock(ticker=f"V{index:02d}", company_name=f"Vol {index}", sector="pharma")
        for index in range(20)
    ]
    db.add_all(stocks)
    await db.commit()
    for stock in stocks:
        await db.refresh(stock)

    now = datetime.now(timezone.utc)
    for index, stock in enumerate(stocks):
        # Calm names compound faster; jumpy names wobble hard and drift less.
        #
        # The wobble's period divides the forward horizon exactly, so the start
        # and end of every measurement window sit at the same phase. A wobble
        # that does not — an every-other-day flip, say — makes the forward
        # return a function of which day the window happened to start on, which
        # swamps the drift and measures nothing.
        if index < 10:
            series = [100.0 * 1.003**day for day in range(220)]
        else:
            series = [
                100.0 * 1.001**day * (1 + 0.12 * math.sin(2 * math.pi * day / 5))
                for day in range(220)
            ]
        await add_prices(db, stock, series, end=now)

    summary = (
        await scoring.validate(
            db, as_of_days_ago=25, horizon_days=15, periods=3, step_days=20
        )
    )["summary"]

    volatility = summary[f"{scoring.FACTOR_PREFIX}volatility_21d"]
    assert volatility["mean_spread"] > 0, volatility
