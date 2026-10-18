"""SEC 8-K item-code expansion.

The submissions feed gives item codes as bare numbers, which carry no words
for the sentiment scorer or event classifier to read. Expanding them to their
official titles is what turns SEC filings from uniformly neutral/other into a
usable signal source.
"""

from __future__ import annotations

import pytest


def test_describe_items_expands_known_codes():
    from app.integrations.sec import describe_items

    assert describe_items("8.01,9.01") == [
        "Item 8.01: Other Events",
        "Item 9.01: Financial Statements and Exhibits",
    ]


def test_describe_items_handles_prefixes_and_blanks():
    from app.integrations.sec import describe_items

    assert describe_items("Item 2.02, ,1.01") == [
        "Item 2.02: Results of Operations and Financial Condition",
        "Item 1.01: Entry into a Material Definitive Agreement",
    ]


def test_describe_items_keeps_unknown_codes():
    from app.integrations.sec import describe_items

    assert describe_items("99.9") == ["Item 99.9"]


@pytest.mark.parametrize(
    "items,expected_event",
    [
        ("2.02", "revenue"),
        ("1.01", "partnership"),
        ("2.01", "merger_acquisition"),
        ("5.02", "exec_change"),
        ("3.02", "capital_raise"),
    ],
)
def test_item_codes_reach_the_event_classifier(items, expected_event):
    """Bare codes carry no words; expanded titles let filings be classified.

    Before the expansion every SEC filing landed as event=other, neutral.
    """
    from app.integrations.sec import FORM_DESCRIPTIONS, describe_items
    from app.services.sentiment import SentimentAnalyzer

    described = describe_items(items)
    headline = f"EXAMPLE INC filed 8-K: {described[0].split(': ', 1)[-1]}"
    body = f"Form 8-K filed 2026-08-07 ({FORM_DESCRIPTIONS['8-K']}). " \
           f"Reported items: {'; '.join(described)}."

    event = SentimentAnalyzer().classify_event_type(headline, body)
    assert event.primary_event.value == expected_event


@pytest.mark.parametrize(
    "items,expected_sentiment",
    [("1.03", "negative"), ("3.01", "negative"), ("2.01", "positive")],
)
def test_item_codes_reach_the_sentiment_scorer(items, expected_sentiment):
    from app.integrations.sec import describe_items
    from app.services.sentiment import SentimentAnalyzer

    described = describe_items(items)
    headline = f"EXAMPLE INC filed 8-K: {described[0].split(': ', 1)[-1]}"
    result = SentimentAnalyzer().analyze_sentiment(headline, "Reported items: " + described[0])
    assert result.sentiment.value == expected_sentiment
