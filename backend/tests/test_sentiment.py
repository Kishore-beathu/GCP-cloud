"""Sentiment scoring and event classification."""

from __future__ import annotations

import pytest

from app.models import EventType, Sentiment
from app.services import sentiment
from app.services.sentiment import LexiconAnalyzer, SentimentAnalyzer


@pytest.fixture
def analyzer() -> SentimentAnalyzer:
    return SentimentAnalyzer(backend="lexicon")


@pytest.mark.parametrize(
    ("headline", "expected"),
    [
        ("FDA approves Moderna's next-generation COVID vaccine", Sentiment.POSITIVE),
        ("Pfizer beats Q3 earnings expectations and raises guidance", Sentiment.POSITIVE),
        ("Phase 3 trial met primary endpoint with statistically significant results", Sentiment.POSITIVE),
        ("FDA rejects application; company receives complete response letter", Sentiment.NEGATIVE),
        ("Company recalls contaminated batches after warning letter", Sentiment.NEGATIVE),
        ("Trial failed to meet primary endpoint, shares plunge", Sentiment.NEGATIVE),
        ("Company to present at healthcare conference next month", Sentiment.NEUTRAL),
    ],
)
def test_sentiment_direction(analyzer: SentimentAnalyzer, headline: str, expected: Sentiment):
    assert analyzer.analyze_sentiment(headline).sentiment is expected


def test_negation_flips_polarity(analyzer: SentimentAnalyzer):
    """'did not meet' must not read as positive just because 'met' appears."""
    result = analyzer.analyze_sentiment("Study did not meet its primary endpoint")
    assert result.sentiment is Sentiment.NEGATIVE


def test_score_bounds_and_confidence(analyzer: SentimentAnalyzer):
    result = analyzer.analyze_sentiment("FDA approves breakthrough therapy designation")
    assert -1.0 <= result.score <= 1.0
    assert 0.0 <= result.confidence <= 1.0
    assert result.model_version == LexiconAnalyzer.model_version


def test_empty_input_is_neutral(analyzer: SentimentAnalyzer):
    result = analyzer.analyze_sentiment("", None)
    assert result.sentiment is Sentiment.NEUTRAL
    assert result.confidence == 0.0


def test_long_body_is_truncated(analyzer: SentimentAnalyzer):
    """A very long body must not blow up or hang the scorer."""
    result = analyzer.analyze_sentiment("FDA approves drug", "filler " * 50_000)
    assert result.sentiment is Sentiment.POSITIVE


@pytest.mark.parametrize(
    ("headline", "expected"),
    [
        ("FDA approves new therapy under priority review", EventType.FDA_APPROVAL),
        ("Company reports Q2 revenue growth and raises full-year guidance", EventType.REVENUE),
        ("AbbVie to acquire biotech in $10B merger", EventType.MERGER_ACQUISITION),
        ("Company recalls lots after FDA warning letter", EventType.RECALL),
        ("Analyst upgrades stock, raises price target", EventType.ANALYST_RATING),
        ("Board appoints new chief executive", EventType.EXEC_CHANGE),
        ("Weather is pleasant in Amsterdam today", EventType.OTHER),
    ],
)
def test_event_classification(analyzer: SentimentAnalyzer, headline: str, expected: EventType):
    assert analyzer.classify_event_type(headline).primary_event is expected


def test_unknown_backend_falls_back_to_lexicon():
    analyzer = SentimentAnalyzer(backend="does-not-exist")
    assert analyzer.model_version == LexiconAnalyzer.model_version


# --- Sector overlays ----------------------------------------------------------
# The lexicon was tuned on pharma and was silent on the vocabulary of the AI and
# storage names added later, which is why those symbols scored a flat 0.0 and
# their sentiment percentiles were ties.


def test_chip_supply_news_is_no_longer_invisible():
    """Real headlines off the dashboard, which the base lexicon scored 0.00.

    Deliberately free of the generic business words the base lexicon already
    knows ("record", "win", "growth"), so this measures the overlay rather than
    the vocabulary both share.
    """
    analyzer = LexiconAnalyzer()
    for headline in (
        "SK Hynix HBM backlog extends as hyperscaler demand climbs",
        "Samsung sold out of HBM capacity through next year",
        "Samsung's $576 Billion Chip Push Gets New Government Backing",
    ):
        assert analyzer.score(headline).score == 0.0, headline
        storage = analyzer.score(headline, lexicon_key="data_storage")
        assert storage.score > 0.5, headline
        assert storage.sentiment is Sentiment.POSITIVE, headline


def test_cycle_downside_is_read_as_negative_for_a_supplier():
    """The overlay has to cut both ways or it is just an optimism dial."""
    analyzer = LexiconAnalyzer()
    headline = "Inventory correction drives severe ASP erosion"

    assert analyzer.score(headline).score == 0.0
    assert analyzer.score(headline, lexicon_key="data_storage").sentiment is Sentiment.NEGATIVE


def test_a_genuinely_neutral_headline_stays_neutral():
    """The overlay must add vocabulary, not manufacture an opinion."""
    analyzer = LexiconAnalyzer()

    result = analyzer.score(
        "Micron vs. SK hynix: One Stock Rules AI Memory", lexicon_key="data_storage"
    )

    assert result.sentiment is Sentiment.NEUTRAL


def test_a_shortage_is_read_as_pricing_power_for_a_supplier():
    """The same word, opposite meaning, decided by whose story it is.

    A drugmaker that cannot supply its product is in trouble. A memory maker
    in a shortage can raise prices. One global weight cannot be right for both.
    """
    analyzer = LexiconAnalyzer()
    headline = "Memory chip shortage could last two more years"

    pharma = analyzer.score(headline, lexicon_key="pharma_life_sciences")
    storage = analyzer.score(headline, lexicon_key="data_storage")

    assert pharma.score < 0
    assert storage.score > 0


def test_the_inverted_term_does_not_fire_on_both_sides_at_once():
    """Left in both sets it would net to zero and read as no opinion."""
    analyzer = LexiconAnalyzer()

    result = analyzer.explain("Severe shortage reported", lexicon_key="data_storage")
    polarities = {match["polarity"] for match in result["matches"] if "shortage" in match["term"]}

    assert polarities == {"positive"}


def test_export_controls_are_negative_for_a_chipmaker():
    analyzer = LexiconAnalyzer()

    result = analyzer.score(
        "New export controls restrict shipments to key customers", lexicon_key="ai"
    )

    assert result.sentiment is Sentiment.NEGATIVE


def test_pharma_scoring_is_unchanged_by_the_overlays():
    """A regression here would be the overlay leaking into the original case."""
    analyzer = LexiconAnalyzer()
    headline = "FDA grants accelerated approval after the trial met its primary endpoint"

    base = analyzer.score(headline)
    pharma = analyzer.score(headline, lexicon_key="pharma_life_sciences")

    assert base.score == pharma.score
    assert base.sentiment is Sentiment.POSITIVE


def test_an_unmapped_sector_gets_the_base_lexicon():
    """Unknown groups must not raise, and must not silently pick an overlay."""
    analyzer = LexiconAnalyzer()
    headline = "Company reports record revenue"

    assert analyzer.score(headline, lexicon_key="other").score == analyzer.score(headline).score
    assert analyzer.score(headline, lexicon_key="not-a-group").score == analyzer.score(
        headline
    ).score


# --- Clinical-stage overlay --------------------------------------------------
# The base lexicon was written pharma-first and reads trial and regulatory
# language well. It was completely silent on how a company with no revenue
# funds itself, which is the single most common thing that cohort announces.


CLINICAL = sentiment.overlay_key("clinical_stage")


@pytest.mark.parametrize(
    "headline",
    [
        "Viking Therapeutics Announces Proposed Public Offering of Common Stock",
        "Sana Biotechnology Announces $150 Million Underwritten Offering of Shares",
        "Editas Medicine Prices $75.0 Million Registered Direct Offering",
        "Fate Therapeutics Announces At-The-Market Equity Distribution Program",
        "Erasca Announces Reverse Stock Split to Regain Nasdaq Compliance",
    ],
)
def test_an_equity_raise_reads_as_bad_news_for_a_pre_revenue_company(headline):
    """Every one of these scored exactly 0.00 before the overlay existed.

    An offering routinely takes 10-20% out of a clinical-stage name in a
    session. Scoring it neutral is not a missing nuance — it is the pillar
    being blind to the most frequent event in the cohort. The word "dilution"
    was already a base negative and never appears; press releases say
    "underwritten public offering".
    """
    analyzer = sentiment.LexiconAnalyzer()

    assert analyzer.score(headline).score == 0.0
    assert analyzer.score(headline, None, CLINICAL).score < 0


def test_a_licensing_deal_counts_for_more_when_there_is_no_product():
    """The same words, weighted by what they mean for the company saying them.

    For a large-cap drugmaker a licensing deal is a Tuesday. For a company with
    no revenue it is validation by a counterparty that did real diligence, and
    usually non-dilutive cash — frequently a larger move than a mid-stage
    readout.
    """
    analyzer = sentiment.LexiconAnalyzer()
    text = "Kymera announces license agreement and collaboration with Sanofi"

    base = analyzer.explain(text)
    clinical = analyzer.explain(text, None, CLINICAL)

    base_weight = sum(match["weight"] for match in base["matches"])
    clinical_weight = sum(match["weight"] for match in clinical["matches"])
    assert clinical_weight > base_weight


def test_a_runway_extension_offsets_the_restructuring_that_paid_for_it():
    """These arrive as one headline, and the base lexicon only saw one half."""
    analyzer = sentiment.LexiconAnalyzer()
    headline = "Beam Therapeutics Extends Cash Runway into 2029 Following Restructuring"

    assert analyzer.score(headline).score < 0
    assert analyzer.score(headline, None, CLINICAL).score > 0


def test_one_phrase_counts_once_however_many_ways_it_could_match():
    """Overlapping patterns inflate the hit count, and confidence reads it.

    The score is a ratio, so double-counting one side does not flip a one-sided
    headline. Confidence is computed from the number of hits, though, so two
    patterns firing on the same six words claim two independent pieces of
    evidence for what is one fact.
    """
    analyzer = sentiment.LexiconAnalyzer()

    explained = analyzer.explain(
        "Announces Proposed Public Offering of Common Stock", None, CLINICAL
    )
    financing = [
        match for match in explained["matches"] if "offering" in match["term"].lower()
    ]

    assert len(financing) == 1


def test_a_large_cap_drugmaker_keeps_the_base_reading():
    """Both sectors live in one group, so the overlay must key on the sector.

    Pfizer issuing debt is ordinary treasury work. If the overlay were keyed at
    the group level it would land on every pharma name in the universe.
    """
    assert sentiment.overlay_key("pharma") == "pharma_life_sciences"
    assert sentiment.overlay_key("clinical_stage") == "clinical_stage"
    assert sentiment.overlay_key("memory") == "data_storage"
    # An unmapped sector still falls through to the base lexicon.
    assert sentiment.overlay_key("nonsense") == "other"

    analyzer = sentiment.LexiconAnalyzer()
    headline = "Pfizer Announces Public Offering of Senior Notes"

    assert analyzer.score(headline, None, sentiment.overlay_key("pharma")).score == 0.0


# --- "Loss" is only a financial word outside medicine -------------------------


@pytest.mark.parametrize(
    "headline",
    [
        "Is the Weight-Loss Drug Market a Winner-Take-All Fight",
        "weight loss drug shows promise in phase 3",
        "vision loss slowed in the treatment arm",
        "muscle loss concerns with GLP-1 therapy",
        "bone loss reversed in the extension study",
        "trial showed loss of vision in the placebo arm",
    ],
)
def test_a_therapeutic_loss_is_not_a_financial_one(headline):
    """Half the indications in this universe are named "loss of something".

    Obesity is the largest theme in pharma right now, and every GLP-1 headline
    says "weight-loss": Lilly and Novo Nordisk were being marked down on their
    own franchise. Ophthalmology, Alzheimer's, sarcopenia and osteoporosis all
    have the same shape.
    """
    analyzer = LexiconAnalyzer()

    matched = [
        match["matched_text"]
        for match in analyzer.explain(headline)["matches"]
        if "loss" in match["matched_text"].lower()
    ]

    assert matched == [], f"{headline!r} scored 'loss' as financial"


@pytest.mark.parametrize(
    "headline",
    [
        "company reports a wider net loss",
        "operating loss widened this quarter",
        "the company posted a loss",
        "quarterly losses narrowed",
    ],
)
def test_a_financial_loss_still_counts(headline):
    """The exclusion must not cost the term its actual job."""
    analyzer = LexiconAnalyzer()

    assert analyzer.score(headline).score < 0
