"""Measured accuracy of the lexicon against a labelled corpus.

These tests are a ratchet: they assert the scorer's accuracy on realistic
headlines rather than checking a handful of hand-picked strings. Lowering a
threshold to make a change pass means the change made the product worse.
"""

from __future__ import annotations

import pytest

from tests.corpus import (
    NEGATIVE,
    NEUTRAL,
    POSITIVE,
    SECTOR_CASES,
    SUBSTRING_TRAPS,
    all_labelled,
)
from app.services.sentiment import LexiconAnalyzer, overlay_key

analyzer = LexiconAnalyzer()


def _label(headline: str) -> str:
    return analyzer.score(headline).sentiment.value


@pytest.mark.parametrize("headline", POSITIVE)
def test_positive_headlines_are_detected(headline: str) -> None:
    """Recall on positive catalysts — the signal the platform exists to find."""
    assert _label(headline) == "positive", analyzer.explain(headline)


@pytest.mark.parametrize("headline", NEGATIVE)
def test_negative_headlines_are_detected(headline: str) -> None:
    assert _label(headline) == "negative", analyzer.explain(headline)


@pytest.mark.parametrize("headline", NEUTRAL)
def test_neutral_headlines_are_not_called_positive(headline: str) -> None:
    """A false positive is worse than a miss: it invites a trade on nothing."""
    assert _label(headline) != "positive", analyzer.explain(headline)


@pytest.mark.parametrize("headline,expected", SUBSTRING_TRAPS)
def test_substring_traps(headline: str, expected: str) -> None:
    """Word-boundary matching: 'submission' must not read as 'miss'."""
    assert _label(headline) == expected, analyzer.explain(headline)


def test_overall_accuracy_stays_high() -> None:
    cases = all_labelled()
    correct = sum(1 for headline, expected in cases if _label(headline) == expected)
    accuracy = correct / len(cases)
    assert accuracy >= 0.95, (
        f"Lexicon accuracy dropped to {accuracy:.0%} on {len(cases)} labelled headlines. "
        "Fix the lexicon rather than lowering this threshold."
    )


def test_no_positive_headline_is_scored_negative() -> None:
    """The costly failure mode: good news read as bad."""
    inverted = [h for h in POSITIVE if _label(h) == "negative"]
    assert not inverted, f"Positive headlines scored negative: {inverted}"


# --- Matching mechanics ------------------------------------------------------


def test_repeated_terms_raise_confidence_but_are_capped() -> None:
    once = analyzer.score("Therapy approved")
    thrice = analyzer.score("Therapy approved; second indication approved; third approved")
    assert thrice.confidence > once.confidence

    # Capped, so one repeated word cannot swamp an opposing signal.
    spammed = analyzer.score(" ".join(["approved"] * 40) + " trial failed to meet endpoint")
    assert spammed.sentiment.value == "positive"
    assert spammed.score < 1.0


def test_negation_does_not_cross_a_clause_boundary() -> None:
    """The negator belongs to the first clause only."""
    result = analyzer.score(
        "Trial did not meet the primary endpoint, but the label expansion was approved"
    )
    matches = {m["matched_text"]: m["negated"] for m in analyzer.explain(
        "Trial did not meet the primary endpoint, but the label expansion was approved"
    )["matches"]}
    assert matches.get("approved") is False


def test_negated_positive_reads_negative() -> None:
    assert _label("Approval was not granted at this review cycle") == "negative"


def test_negated_negative_reads_positive() -> None:
    assert _label("No safety concerns were observed in the study") == "positive"


def test_explain_reports_the_terms_that_fired() -> None:
    detail = analyzer.explain("FDA grants priority review")
    assert detail["sentiment"] == "positive"
    assert any(m["matched_text"] == "priority review" for m in detail["matches"])
    assert all({"term", "matched_text", "weight", "polarity"} <= set(m) for m in detail["matches"])


def test_empty_input_is_neutral() -> None:
    assert analyzer.score("").sentiment.value == "neutral"
    assert analyzer.score("   ").sentiment.value == "neutral"


# --- Beyond pharma ------------------------------------------------------------
# Five terms have been found meaning one thing in a filing and another in a
# release note — loss, trial, upgrade, recall, probe. Each silently corrupted
# scores until it was noticed on the dashboard, and none was caught here,
# because the corpus contained no headline that could catch it.


@pytest.mark.parametrize("headline,expected,sector", SECTOR_CASES)
def test_sector_headlines_are_scored_correctly(
    headline: str, expected: str, sector: str
) -> None:
    """Scored through the overlay the symbol would actually arrive under.

    A memory maker's "shortage" read with the pharma lexicon measures the
    wrong thing, so the sector travels with the case.
    """
    key = overlay_key(sector)
    label = analyzer.score(headline, None, key).sentiment.value

    assert label == expected, analyzer.explain(headline, None, key)


def test_the_whole_corpus_including_sector_cases_stays_accurate() -> None:
    """One ratchet over everything, so a sector fix cannot regress pharma."""
    cases = [(h, e, None) for h, e in all_labelled()]
    cases += [(h, e, overlay_key(s)) for h, e, s in SECTOR_CASES]

    correct = sum(
        1
        for headline, expected, key in cases
        if analyzer.score(headline, None, key).sentiment.value == expected
    )
    accuracy = correct / len(cases)

    assert accuracy >= 0.95, (
        f"Lexicon accuracy dropped to {accuracy:.0%} on {len(cases)} labelled "
        "headlines. Fix the lexicon rather than lowering this threshold."
    )
