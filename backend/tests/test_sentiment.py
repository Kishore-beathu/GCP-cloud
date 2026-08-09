"""Sentiment scoring and event classification."""

from __future__ import annotations

import pytest

from app.models import EventType, Sentiment
from app.services.sentiment import SentimentAnalyzer


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
    assert result.model_version == "lexicon-v1"


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
    assert analyzer.model_version == "lexicon-v1"
