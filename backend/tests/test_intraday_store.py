"""Recording intraday bars, the prerequisite for measuring the setups.

`app/services/setups.py` states that its four setups have an unknown hit rate
and cannot be validated, because nothing kept a record of what a five-minute
chart looked like at 09:47 last Tuesday. These tests cover the half that fixes
that: storing the bars, and doing it in a way that survives a missed run.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.integrations.yahoo import Bar, YahooUnavailable

pytestmark = pytest.mark.asyncio


def _bars(count: int, start: datetime | None = None) -> list[Bar]:
    origin = start or datetime(2026, 9, 2, 13, 30, tzinfo=timezone.utc)
    return [
        Bar(
            at=origin + timedelta(minutes=5 * n),
            close=100.0 + n,
            open=99.5 + n,
            high=100.5 + n,
            low=99.0 + n,
            volume=1000 + n,
        )
        for n in range(count)
    ]


async def test_a_session_is_recorded(db, seeded_stocks, monkeypatch):
    from app.services import intraday_store

    monkeypatch.setattr(intraday_store, "REQUEST_DELAY_SECONDS", 0.0)

    async def _fetch(ticker: str, window: str):
        assert window == intraday_store.RECORD_WINDOW
        return _bars(6)

    monkeypatch.setattr(intraday_store, "fetch_intraday", _fetch)

    report = await intraday_store.record_intraday(db, [seeded_stocks[0].ticker])

    assert report.symbols == 1
    assert report.bars_seen == 6
    assert report.bars_stored == 6
    assert report.bars_already_known == 0


async def test_re_recording_the_same_session_stores_nothing_new(
    db, seeded_stocks, monkeypatch
):
    """A run re-reads the whole session, so a missed run heals itself.

    That only works if storing a bar twice is a no-op. If it were not, every
    fifteen-minute pass would duplicate the entire day so far, and by the
    close a morning bar would exist thirty times over.
    """
    from app.services import intraday_store

    monkeypatch.setattr(intraday_store, "REQUEST_DELAY_SECONDS", 0.0)
    monkeypatch.setattr(intraday_store, "fetch_intraday", lambda t, w: _ret(_bars(6)))

    await intraday_store.record_intraday(db, [seeded_stocks[0].ticker])
    second = await intraday_store.record_intraday(db, [seeded_stocks[0].ticker])

    assert second.bars_seen == 6
    assert second.bars_stored == 0
    assert second.bars_already_known == 6


async def test_a_later_run_stores_only_the_new_bars(db, seeded_stocks, monkeypatch):
    """The session grows during the day; only its tail is new."""
    from app.services import intraday_store

    monkeypatch.setattr(intraday_store, "REQUEST_DELAY_SECONDS", 0.0)

    monkeypatch.setattr(intraday_store, "fetch_intraday", lambda t, w: _ret(_bars(4)))
    await intraday_store.record_intraday(db, [seeded_stocks[0].ticker])

    monkeypatch.setattr(intraday_store, "fetch_intraday", lambda t, w: _ret(_bars(7)))
    later = await intraday_store.record_intraday(db, [seeded_stocks[0].ticker])

    assert later.bars_stored == 3
    assert later.bars_already_known == 4


async def test_bars_come_back_in_the_shape_the_setups_expect(
    db, seeded_stocks, monkeypatch
):
    """Stored history must be handed to the evaluators unchanged.

    The setups read highs, lows and volume — an opening range is built from
    highs and lows, relative volume needs volume. Returning close-only rows
    would store the data and still leave the setups unmeasurable.
    """
    from app.services import intraday_store

    monkeypatch.setattr(intraday_store, "REQUEST_DELAY_SECONDS", 0.0)
    monkeypatch.setattr(intraday_store, "fetch_intraday", lambda t, w: _ret(_bars(5)))
    await intraday_store.record_intraday(db, [seeded_stocks[0].ticker])

    stored = await intraday_store.stored_bars(
        db,
        seeded_stocks[0].id,
        datetime(2026, 9, 2, tzinfo=timezone.utc),
        datetime(2026, 9, 3, tzinfo=timezone.utc),
    )

    assert len(stored) == 5
    assert [bar.close for bar in stored] == [100.0, 101.0, 102.0, 103.0, 104.0]
    first = stored[0]
    assert first.open == 99.5 and first.high == 100.5 and first.low == 99.0
    assert first.volume == 1000
    # Oldest first, as the evaluators assume.
    assert stored == sorted(stored, key=lambda bar: bar.at)


async def test_a_symbol_the_vendor_lacks_is_named_not_counted(
    db, seeded_stocks, monkeypatch
):
    """A count cannot separate a delisted symbol from an outage."""
    from app.services import intraday_store

    monkeypatch.setattr(intraday_store, "REQUEST_DELAY_SECONDS", 0.0)
    monkeypatch.setattr(intraday_store, "fetch_intraday", lambda t, w: _ret([]))

    report = await intraday_store.record_intraday(db, [seeded_stocks[0].ticker])

    assert report.uncovered == [seeded_stocks[0].ticker]
    assert report.bars_stored == 0


async def test_being_rate_limited_stops_the_run_rather_than_continuing(
    db, seeded_stocks, monkeypatch
):
    """A refusal applies to every symbol after this one too.

    Spending the rest of the run being told the same thing is how a rate limit
    becomes a block — the same fault that cost the whole SEC backfill earlier.
    """
    from app.services import intraday_store

    monkeypatch.setattr(intraday_store, "REQUEST_DELAY_SECONDS", 0.0)
    attempts = 0

    async def _refuse(ticker: str, window: str):
        nonlocal attempts
        attempts += 1
        raise YahooUnavailable("HTTP 429 - being rate limited")

    monkeypatch.setattr(intraday_store, "fetch_intraday", _refuse)

    report = await intraday_store.record_intraday(
        db, [stock.ticker for stock in seeded_stocks]
    )

    assert attempts == 1
    assert report.failures == {"YahooUnavailable": 1}


async def test_one_bad_symbol_does_not_lose_the_others(
    db, seeded_stocks, monkeypatch
):
    """Unlike a refusal, a single broken symbol says nothing about the rest."""
    from app.services import intraday_store

    monkeypatch.setattr(intraday_store, "REQUEST_DELAY_SECONDS", 0.0)
    first = seeded_stocks[0].ticker

    async def _mixed(ticker: str, window: str):
        if ticker == first:
            raise ValueError("malformed payload")
        return _bars(3)

    monkeypatch.setattr(intraday_store, "fetch_intraday", _mixed)

    report = await intraday_store.record_intraday(
        db, [stock.ticker for stock in seeded_stocks]
    )

    assert report.failures == {"ValueError": 1}
    assert report.bars_stored == 3


async def test_bars_past_the_retention_window_are_purged(
    db, seeded_stocks, monkeypatch
):
    """Five-minute bars accumulate at roughly 78 rows per symbol per day."""
    from app.models import IntradayBar
    from app.services import intraday_store

    stale = datetime.now(timezone.utc) - timedelta(
        days=intraday_store.RETENTION_DAYS + 5
    )
    db.add(
        IntradayBar(
            ticker_id=seeded_stocks[0].id, interval="5m", at=stale, close=42.0
        )
    )
    await db.commit()

    monkeypatch.setattr(intraday_store, "REQUEST_DELAY_SECONDS", 0.0)
    monkeypatch.setattr(
        intraday_store,
        "fetch_intraday",
        lambda t, w: _ret(_bars(2, datetime.now(timezone.utc))),
    )

    report = await intraday_store.record_intraday(db, [seeded_stocks[0].ticker])

    assert report.purged == 1
    assert report.bars_stored == 2


async def test_coverage_reports_progress_toward_measurable(
    db, seeded_stocks, monkeypatch
):
    """Until enough sessions exist there is nothing to measure; say how far along."""
    from app.services import intraday_store

    monkeypatch.setattr(intraday_store, "REQUEST_DELAY_SECONDS", 0.0)
    monkeypatch.setattr(intraday_store, "fetch_intraday", lambda t, w: _ret(_bars(8)))
    await intraday_store.record_intraday(db, [seeded_stocks[0].ticker])

    summary = await intraday_store.coverage(db)

    assert summary["bars"] == 8
    assert summary["symbols"] == 1
    assert summary["earliest"] is not None
    assert summary["retention_days"] == intraday_store.RETENTION_DAYS


async def test_the_endpoint_records_and_reports_coverage(
    client, seeded_stocks, monkeypatch
):
    from app.services import intraday_store

    monkeypatch.setattr(intraday_store, "REQUEST_DELAY_SECONDS", 0.0)
    monkeypatch.setattr(intraday_store, "fetch_intraday", lambda t, w: _ret(_bars(4)))

    response = await client.post(
        "/admin/ingest/intraday", params={"ticker": seeded_stocks[0].ticker}
    )
    assert response.status_code == 200
    body = response.json()
    assert body["bars_stored"] == 4
    assert body["interval"] == "5m"

    coverage = await client.get("/admin/intraday/coverage")
    assert coverage.status_code == 200
    assert coverage.json()["bars"] == 4


async def _ret(value):
    return value
