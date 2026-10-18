"""The forward calendar: what is scheduled, and how firmly it is known.

Everything else in this platform looks backwards. A catalyst is the opposite —
known in advance, unresolved — which makes it the only thing that answers
"what should I be watching tomorrow". The tests here are mostly about honesty:
a sponsor's estimate and a confirmed earnings date must not be presented as
the same kind of claim.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.models import CatalystEvent, Stock
from app.services import catalysts


def test_a_month_only_date_is_read_as_the_first_of_that_month():
    """ClinicalTrials.gov often gives "2026-09" and no day.

    Discarding it would lose a readout that is genuinely known to be coming;
    `confidence` already says the whole date is an estimate.
    """
    assert catalysts._parse_partial_date("2026-09-14") == datetime(
        2026, 9, 14, tzinfo=timezone.utc
    )
    assert catalysts._parse_partial_date("2026-09") == datetime(
        2026, 9, 1, tzinfo=timezone.utc
    )
    assert catalysts._parse_partial_date("") is None
    assert catalysts._parse_partial_date("September 2026") is None


@pytest.mark.asyncio
async def test_upcoming_returns_events_soonest_first(db, seeded_stocks):
    stock = seeded_stocks[0]
    now = datetime.now(timezone.utc)
    db.add_all(
        [
            CatalystEvent(
                ticker_id=stock.id,
                kind="trial_readout",
                expected_at=now + timedelta(days=5),
                confidence="estimated",
                title="Phase 3 primary completion",
                source="clinicaltrials",
            ),
            CatalystEvent(
                ticker_id=stock.id,
                kind="earnings",
                expected_at=now + timedelta(days=2),
                confidence="confirmed",
                title="Q3 earnings",
                source="finnhub",
            ),
        ]
    )
    await db.commit()

    events = await catalysts.upcoming(db, days=10)

    assert [event["kind"] for event in events] == ["earnings", "trial_readout"]
    assert events[0]["days_away"] == 2


@pytest.mark.asyncio
async def test_confidence_distinguishes_a_confirmed_date_from_an_estimate(db, seeded_stocks):
    """A trial completion date slips routinely; an earnings date does not.

    Presenting both as "scheduled" would be misleading about both.
    """
    stock = seeded_stocks[0]
    now = datetime.now(timezone.utc)
    db.add_all(
        [
            CatalystEvent(
                ticker_id=stock.id, kind="earnings", expected_at=now + timedelta(days=1),
                confidence="confirmed", title="Q3 earnings", source="finnhub",
            ),
            CatalystEvent(
                ticker_id=stock.id, kind="trial_readout", expected_at=now + timedelta(days=3),
                confidence="estimated", title="Readout", source="clinicaltrials",
            ),
        ]
    )
    await db.commit()

    by_kind = {event["kind"]: event for event in await catalysts.upcoming(db, days=10)}

    assert by_kind["earnings"]["confidence"] == "confirmed"
    assert by_kind["trial_readout"]["confidence"] == "estimated"


@pytest.mark.asyncio
async def test_past_events_are_not_reported_as_upcoming(db, seeded_stocks):
    stock = seeded_stocks[0]
    now = datetime.now(timezone.utc)
    db.add(
        CatalystEvent(
            ticker_id=stock.id, kind="earnings", expected_at=now - timedelta(days=1),
            confidence="confirmed", title="Already happened", source="finnhub",
        )
    )
    await db.commit()

    assert await catalysts.upcoming(db, days=10) == []


@pytest.mark.asyncio
async def test_refreshing_replaces_a_moved_date_rather_than_duplicating(db, seeded_stocks, monkeypatch):
    """A date that moves must not leave a ghost at the old one."""
    stock = seeded_stocks[0]
    now = datetime.now(timezone.utc)
    db.add(
        CatalystEvent(
            ticker_id=stock.id, kind="earnings", expected_at=now + timedelta(days=3),
            confidence="confirmed", title="Old date", source="finnhub",
        )
    )
    await db.commit()

    async def _moved(by_symbol, api_key, start, until):
        return [
            CatalystEvent(
                ticker_id=stock.id, kind="earnings", expected_at=now + timedelta(days=6),
                confidence="confirmed", title="New date", source="finnhub",
            )
        ]

    async def _no_trials(db_, start, until):
        return []

    from app.config import get_settings

    monkeypatch.setenv("FINNHUB_API_KEY", "test-key")
    get_settings.cache_clear()
    monkeypatch.setattr(catalysts, "_earnings_events", _moved)
    monkeypatch.setattr(catalysts, "_trial_readouts", _no_trials)

    try:
        report = await catalysts.refresh_calendar(db)
    finally:
        get_settings.cache_clear()

    assert report.earnings == 1
    events = await catalysts.upcoming(db, days=30)
    assert len(events) == 1
    assert events[0]["title"] == "New date"


@pytest.mark.asyncio
async def test_one_failed_source_does_not_sink_the_calendar(db, seeded_stocks, monkeypatch):
    """The trials lookup going down should not cost you the earnings dates."""
    stock = seeded_stocks[0]
    now = datetime.now(timezone.utc)

    async def _earnings(by_symbol, api_key, start, until):
        return [
            CatalystEvent(
                ticker_id=stock.id, kind="earnings", expected_at=now + timedelta(days=2),
                confidence="confirmed", title="Q3", source="finnhub",
            )
        ]

    async def _broken(db_, start, until):
        raise RuntimeError("ClinicalTrials.gov is down")

    from app.config import get_settings

    monkeypatch.setenv("FINNHUB_API_KEY", "test-key")
    get_settings.cache_clear()
    monkeypatch.setattr(catalysts, "_earnings_events", _earnings)
    monkeypatch.setattr(catalysts, "_trial_readouts", _broken)

    try:
        report = await catalysts.refresh_calendar(db)
    finally:
        get_settings.cache_clear()

    assert report.earnings == 1
    assert report.sources_failed == ["clinicaltrials"]


@pytest.mark.asyncio
async def test_the_calendar_endpoint_states_what_it_cannot_know(client, db, seeded_stocks):
    """PDUFA dates are the most valuable catalyst here and are absent.

    There is no free structured feed for them, and parsing one out of a press
    release would put a confident date on a guess.
    """
    body = (await client.get("/calendar?days=7")).json()

    assert "PDUFA" in body["caveat"]
    assert "estimate" in body["caveat"]
