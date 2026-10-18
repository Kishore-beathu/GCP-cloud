"""Unadjusted corporate actions: a correctness bug with a long fuse.

A 2-for-1 split halves the close overnight and every price factor reads it as
a 50% crash. The backtester reads the period as a catastrophe, and because
pillar weights are set from what the backtester reports, one split inside a
validation window quietly moves the weights of the whole score.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from app.models import Stock, StockPrice
from app.services import corporate_actions


@pytest.mark.parametrize(
    ("previous", "close", "expected"),
    [
        (100.0, 50.0, "2:1"),
        (90.0, 30.0, "3:1"),
        (90.0, 60.0, "3:2"),
        (2.0, 20.0, "1:10 reverse"),
        (5.0, 10.0, "1:2 reverse"),
    ],
)
def test_common_split_ratios_are_recognised(previous, close, expected):
    _, matched = corporate_actions.match_ratio(previous, close)

    assert matched == expected


@pytest.mark.parametrize(
    ("previous", "close"),
    [
        (100.0, 42.0),   # a failed readout: a real 58% fall
        (100.0, 75.0),   # a bad quarter
        (100.0, 88.0),   # an ordinary day
        (100.0, 130.0),  # a takeover bid
    ],
)
def test_a_real_move_is_not_mistaken_for_a_split(previous, close):
    """This universe is full of biotechs that genuinely halve on bad news.

    Size alone cannot separate a crash from a split. Roundness can, and that
    is the entire test.
    """
    assert corporate_actions.is_suspicious(previous, close) is False


def test_the_smallest_split_in_the_table_sets_the_threshold():
    """3:2 is a 33% move.

    An earlier version gated on size *as well* as roundness, at 40% — which
    silently excluded the smallest and one of the most common splits there is.
    """
    assert corporate_actions.is_suspicious(90.0, 60.0) is True


@pytest.mark.asyncio
async def test_a_split_in_stored_history_is_found_and_named(db):
    stock = Stock(ticker="MU", company_name="Micron", sector="memory")
    db.add(stock)
    await db.commit()
    await db.refresh(stock)

    start = datetime.now(timezone.utc) - timedelta(days=30)
    closes = [100.0, 101.0, 102.0, 51.0, 51.5, 52.0]
    for index, close in enumerate(closes):
        db.add(
            StockPrice(
                ticker_id=stock.id,
                close=close,
                price_date=start + timedelta(days=index),
                source="test",
            )
        )
    await db.commit()

    report = await corporate_actions.detect(db)

    assert len(report.suspected) == 1
    found = report.suspected[0]
    assert found["ticker"] == "MU"
    assert found["matched_ratio"] == "2:1"
    assert found["previous_close"] == 102.0
    assert found["close"] == 51.0


@pytest.mark.asyncio
async def test_a_genuine_crash_is_counted_separately_from_a_split(db):
    """Both are large moves; only one is a data problem.

    Folding them together would inflate the split count with every bad day in
    a universe built around binary events.
    """
    stock = Stock(ticker="SAVA", company_name="Clinical Co", sector="clinical_stage")
    db.add(stock)
    await db.commit()
    await db.refresh(stock)

    start = datetime.now(timezone.utc) - timedelta(days=10)
    for index, close in enumerate([100.0, 100.0, 42.0, 41.0]):
        db.add(
            StockPrice(
                ticker_id=stock.id,
                close=close,
                price_date=start + timedelta(days=index),
                source="test",
            )
        )
    await db.commit()

    report = await corporate_actions.detect(db)

    assert report.suspected == []
    assert report.unmatched_moves == 1


@pytest.mark.asyncio
async def test_the_report_says_nothing_was_adjusted(db):
    """The output is a list to confirm, not a correction that has been applied."""
    report = await corporate_actions.detect(db)

    assert "nothing is adjusted" in report.as_dict()["caveat"].lower()
