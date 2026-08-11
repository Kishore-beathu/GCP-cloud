"""The four intraday setups, against sessions built to contain the pattern.

Each setup is a checklist, so each is tested twice: once on a session that
satisfies it, and once on a session that breaks exactly one condition. The
second half is the important half — a checklist that fires on everything is
worth nothing, and the tests name which condition did the rejecting.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone


import pytest
from app.integrations.yahoo import Bar
from app.services import setups

OPEN = datetime(2026, 8, 11, 13, 30, tzinfo=timezone.utc)


def bar(minute: int, close: float, **kwargs) -> Bar:
    return Bar(
        at=OPEN + timedelta(minutes=minute),
        close=close,
        open=kwargs.get("open", close),
        high=kwargs.get("high", close),
        low=kwargs.get("low", close),
        volume=kwargs.get("volume", 1000),
    )


def failed(evaluation: setups.Evaluation) -> list[str]:
    return evaluation.failed


def now_ending(session: list[Bar]) -> list[Bar]:
    """Shift a session so its last bar is a minute old.

    The scanner skips symbols whose newest bar is stale, because most of this
    universe is listed outside the US and those markets are shut during the US
    session. A fixture pinned to a fixed timestamp is stale by construction, so
    anything testing the *live* path has to sit in the present.
    """
    shift = (datetime.now(timezone.utc) - timedelta(minutes=1)) - session[-1].at
    return [
        Bar(
            at=item.at + shift,
            close=item.close,
            open=item.open,
            high=item.high,
            low=item.low,
            volume=item.volume,
        )
        for item in session
    ]


# --- Position sizing ---------------------------------------------------------


def test_size_is_decided_by_the_stop_distance():
    """The whole of "low risk": a wider stop buys fewer shares.

    The plan's worked example — a $20,000 account, 0.5% risk, a $0.50 stop —
    should come out at 200 shares.
    """
    result = setups.position_size(20_000, entry=120.20, stop=119.70)

    assert result["shares"] == 200
    assert result["risk_budget"] == 100.0
    assert result["actual_risk"] == 100.0


def test_a_wider_stop_buys_proportionally_fewer_shares():
    tight = setups.position_size(20_000, entry=120.0, stop=119.5)
    wide = setups.position_size(20_000, entry=120.0, stop=118.0)

    assert wide["shares"] < tight["shares"]
    # Both put the same amount at stake, which is the point.
    assert wide["actual_risk"] <= 100.0 and tight["actual_risk"] <= 100.0


def test_a_stop_at_the_entry_is_refused_rather_than_dividing_by_zero():
    assert setups.position_size(20_000, entry=100.0, stop=100.0)["shares"] == 0


# --- L1: dip and rip ---------------------------------------------------------


def _dip_and_rip_session() -> list[Bar]:
    """Open, pull back but hold VWAP, then reclaim the range on volume."""
    return [
        bar(0, 101.0, high=101.5, low=100.0, volume=5000),
        bar(5, 101.8, high=102.0, low=101.0, volume=4000),
        bar(10, 101.5, high=101.9, low=101.2, volume=3000),
        bar(15, 101.0, high=101.4, low=100.8, volume=1500),   # pullback, light
        bar(20, 100.9, high=101.1, low=100.7, volume=1200),
        bar(25, 101.2, high=101.3, low=100.8, volume=1300),
        bar(30, 103.0, high=103.2, low=101.2, volume=6000),   # reclaim, heavy
    ]


def test_dip_and_rip_fires_on_a_reclaim_above_the_opening_range():
    evaluation = setups.dip_and_rip("MU", _dip_and_rip_session(), previous_close=100.0)

    assert evaluation.signal is not None, failed(evaluation)
    signal = evaluation.signal
    assert signal.direction == "long"
    assert signal.entry == 103.0
    assert signal.stop < signal.entry
    # The default target is 2R, and the floor is enforced centrally.
    assert signal.reward_risk >= setups.MIN_REWARD_RISK


def test_dip_and_rip_does_not_fire_below_the_prior_close():
    """"Up on the day" is the first condition, not a nicety."""
    evaluation = setups.dip_and_rip("MU", _dip_and_rip_session(), previous_close=110.0)

    assert evaluation.signal is None
    assert "up on the day" in failed(evaluation)


def test_dip_and_rip_does_not_fire_without_volume_confirmation():
    """A drift through the level is not a breakout."""
    session = _dip_and_rip_session()
    session[-1] = bar(30, 103.0, high=103.2, low=101.2, volume=800)

    evaluation = setups.dip_and_rip("MU", session, previous_close=100.0)

    assert evaluation.signal is None
    assert any("volume" in name for name in failed(evaluation))


def test_dip_and_rip_does_not_fire_when_price_lost_vwap():
    session = _dip_and_rip_session()
    session[-1] = bar(30, 99.0, high=101.0, low=98.9, volume=6000)

    evaluation = setups.dip_and_rip("MU", session, previous_close=98.0)

    assert evaluation.signal is None
    assert "holding above VWAP" in failed(evaluation)


# --- L2: VWAP bounce ---------------------------------------------------------


def _uptrend_session() -> list[Bar]:
    """Higher lows, a pullback that touches VWAP, then a bullish bounce.

    The session high sits above the bounce, so there is somewhere to aim: with
    the high at the entry bar the trade has no target and cannot clear 2R.
    """
    return [
    bar(0,  100.0, high=100.2, low=99.8,  volume=3000),
    bar(5,  100.6, high=100.8, low=100.2, volume=3000),
    bar(10, 101.2, high=101.4, low=100.7, volume=3000),
    bar(15, 101.0, high=101.3, low=100.8, volume=2000),
    bar(20, 101.8, high=102.0, low=100.9, volume=3000),
    bar(25, 102.6, high=102.8, low=101.6, volume=3000),
    bar(30, 103.4, high=104.8, low=102.5, volume=4000),   # session high 104.8
    bar(35, 102.8, high=103.4, low=102.6, volume=2000),
    bar(40, 102.2, high=102.7, low=102.0, volume=1800),
    bar(45, 101.9, high=102.3, low=101.7, volume=1600),
    bar(50, 101.8, high=102.0, low=101.5, volume=1500),   # pullback into VWAP
    bar(55, 102.4, open=101.8, high=102.5, low=101.6, volume=3000),  # bounce
    ]


def test_vwap_bounce_fires_on_a_bullish_bounce_off_value():
    evaluation = setups.vwap_bounce("STX", _uptrend_session())

    assert evaluation.signal is not None, failed(evaluation)
    signal = evaluation.signal
    assert signal.direction == "long"
    # Stop under the swing low that formed at value.
    assert signal.stop < signal.entry


def test_vwap_bounce_does_not_fire_on_a_bearish_candle():
    """"Shows signs of support" is a bullish close, not merely a touch."""
    session = _uptrend_session()
    session[-1] = bar(55, 101.0, open=102.0, high=102.1, low=100.9, volume=3000)

    evaluation = setups.vwap_bounce("STX", session)

    assert evaluation.signal is None


def test_vwap_bounce_needs_an_uptrend_not_just_a_touch():
    """Buying a VWAP touch in a downtrend is catching a falling knife."""
    downtrend = [
        bar(index * 5, 105.0 - index * 0.5, high=105.2 - index * 0.5, low=104.8 - index * 0.5)
        for index in range(12)
    ]

    evaluation = setups.vwap_bounce("STX", downtrend)

    assert evaluation.signal is None
    assert "uptrend (higher lows)" in failed(evaluation)


# --- S1: failed breakout -----------------------------------------------------


def _failed_breakout_session() -> list[Bar]:
    """A level is taken, immediately rejected, and given back on volume."""
    return [
    bar(0,  99.5,  high=99.7,  low=99.3,  volume=6000),
    bar(5,  100.0, high=100.2, low=99.6,  volume=6000),
    bar(10, 100.6, high=100.9, low=100.2, volume=5000),
    bar(15, 101.4, high=101.7, low=100.9, volume=4000),
    bar(20, 102.0, high=102.2, low=101.5, volume=4000),   # resistance 102.2
    bar(25, 101.7, high=102.0, low=101.5, volume=2000),
    bar(30, 101.8, high=102.1, low=101.6, volume=1800),
    bar(35, 102.0, high=102.5, low=101.8, volume=2000),   # pokes above 102.2
    bar(40, 101.9, high=102.3, low=101.7, volume=1700),
    bar(45, 101.95,high=102.15,low=101.8, volume=1600),
    bar(50, 101.9, high=102.0, low=101.8, volume=1500),
    bar(55, 101.8, open=101.95, high=102.0, low=101.7, volume=3000),  # give-back
    ]


def test_failed_breakout_fires_when_the_level_is_given_back():
    evaluation = setups.failed_breakout("WDC", _failed_breakout_session())

    assert evaluation.signal is not None, failed(evaluation)
    signal = evaluation.signal
    assert signal.direction == "short"
    # Stop above the level that failed; target back at value.
    assert signal.stop > signal.entry
    assert signal.target < signal.entry


def test_failed_breakout_does_not_fire_if_the_level_was_never_broken():
    """Otherwise every pullback in an uptrend reads as a failed breakout."""
    session = _failed_breakout_session()
    # Cap every attempt below the level, not just the most obvious one — two
    # bars poked above it, and removing one still leaves a broken level.
    for index in range(len(session) - setups.ATTEMPT_BARS, len(session)):
        old = session[index]
        session[index] = bar(
            index * 5,
            min(old.close, 102.0),
            open=min(old.open, 102.0),
            high=min(old.high, 102.0),
            low=old.low,
            volume=old.volume,
        )

    evaluation = setups.failed_breakout("WDC", session)

    assert evaluation.signal is None
    assert "level was broken" in failed(evaluation)


def test_failed_breakout_does_not_fire_on_a_bullish_last_bar():
    session = _failed_breakout_session()
    session[-1] = bar(55, 101.3, open=100.9, high=101.4, low=100.8, volume=3000)

    evaluation = setups.failed_breakout("WDC", session)

    assert evaluation.signal is None
    assert "rejection bar is bearish" in failed(evaluation)


# --- S2: parabolic extension -------------------------------------------------


def _parabolic_session() -> list[Bar]:
    """A spike far above value, rejected with a long wick, then rolling over.

    The base is well below the spike on purpose. A stop above a blow-off high
    is wide by construction, so the trade only clears the 2R floor when the
    move genuinely came from far below — which is what "parabolic" means.
    """
    return [
    bar(0,  95.0,  high=95.2,  low=94.8,  volume=8000),
    bar(5,  96.0,  high=96.3,  low=95.0,  volume=8000),
    bar(10, 97.5,  high=97.8,  low=95.9,  volume=8000),
    bar(15, 99.0,  high=99.4,  low=97.4,  volume=8000),
    bar(20, 101.4, high=101.6, low=98.9,  volume=8000),
    bar(25, 106.0, open=101.5, high=112.0, low=101.4, volume=6000),  # blow-off
    bar(30, 108.5, open=106.0, high=109.0, low=105.5, volume=3000),  # lower high
    bar(35, 107.8, open=108.5, high=108.8, low=107.5, volume=2500),
    bar(40, 107.4, open=107.8, high=107.9, low=107.1, volume=2500),
    bar(45, 107.0, open=107.4, high=107.5, low=106.8, volume=2500),
    bar(50, 106.8, open=107.0, high=107.1, low=106.6, volume=2500),
    bar(55, 106.5, open=106.8, high=106.9, low=106.3, volume=2500),
    ]


def test_parabolic_short_fires_only_after_the_move_has_rolled_over():
    evaluation = setups.parabolic_short("SNDK", _parabolic_session())

    assert evaluation.signal is not None, failed(evaluation)
    signal = evaluation.signal
    assert signal.direction == "short"
    assert signal.stop > signal.entry  # above the blow-off high


def test_parabolic_short_does_not_fire_while_price_is_still_making_highs():
    """The plan is explicit that this must not be taken early."""
    session = _parabolic_session()
    session.append(bar(60, 113.0, open=103.0, high=113.5, low=102.9, volume=9000))

    evaluation = setups.parabolic_short("SNDK", session)

    assert evaluation.signal is None
    assert "rolled over (lower high since)" in failed(evaluation)


def test_parabolic_short_does_not_fire_without_a_rejection_wick():
    """A clean push to a high is not exhaustion."""
    session = _parabolic_session()
    session[5] = bar(25, 111.8, open=101.5, high=112.0, low=101.4, volume=9000)

    evaluation = setups.parabolic_short("SNDK", session)

    assert evaluation.signal is None
    assert "rejection wick on the spike" in failed(evaluation)


def test_parabolic_short_does_not_fire_on_an_ordinary_trend():
    """"Far above VWAP" is relative to the session's own behaviour."""
    steady = [
        bar(index * 5, 100.0 + index * 0.1, high=100.2 + index * 0.1, low=99.9 + index * 0.1)
        for index in range(14)
    ]

    evaluation = setups.parabolic_short("SNDK", steady)

    assert evaluation.signal is None


# --- The checklist contract --------------------------------------------------


def test_every_setup_reports_its_checks_even_when_it_does_not_fire():
    """A pass/fail with no reason cannot be acted on or debugged."""
    flat = [bar(index * 5, 100.0) for index in range(20)]

    for evaluation in setups.evaluate_all("MU", flat, previous_close=100.0):
        assert evaluation.checks, f"{evaluation.setup} reported nothing"
        assert all(check.detail for check in evaluation.checks)
        payload = evaluation.as_dict()
        assert payload["triggered"] is False
        assert payload["failed_checks"]


def test_a_signal_never_ships_below_the_reward_risk_floor():
    """Enforced centrally so no setup can quietly skip it."""
    for evaluation in setups.evaluate_all("MU", _dip_and_rip_session(), previous_close=100.0):
        if evaluation.signal:
            assert evaluation.signal.reward_risk >= setups.MIN_REWARD_RISK


# --- The scanner endpoint ----------------------------------------------------


@pytest.mark.asyncio
async def test_scan_requires_a_target(client, seeded_stocks):
    """Scanning everything would mean one live request per symbol."""
    response = await client.get("/setups")

    assert response.status_code == 422
    assert "group" in response.json()["detail"]


@pytest.mark.asyncio
async def test_scan_rejects_an_unknown_group(client, seeded_stocks):
    response = await client.get("/setups?group=data_storge")

    assert response.status_code == 422


@pytest.mark.asyncio
async def test_scan_rejects_an_unknown_setup(client, seeded_stocks):
    response = await client.get("/setups?ticker=MRNA&setup=L9")

    assert response.status_code == 422
    assert "L1" in response.json()["detail"]


@pytest.mark.asyncio
async def test_scan_reports_symbols_it_could_not_read(client, seeded_stocks, monkeypatch):
    """"No signals" and "no data" must not look the same."""

    async def _no_bars(symbol, window):
        return []

    monkeypatch.setattr("app.routers.setups.fetch_intraday", _no_bars)

    body = (await client.get("/setups?ticker=MRNA")).json()

    assert body["signals"] == []
    assert body["unavailable"] == ["MRNA"]
    assert body["scanned"] == 0


@pytest.mark.asyncio
async def test_scan_returns_a_signal_with_its_size(client, db, seeded_stocks, monkeypatch):
    """The whole deliverable: a level-based trade, sized to the risk budget."""
    from datetime import datetime as dt

    from app.models import StockPrice

    stock = seeded_stocks[0]
    db.add(
        StockPrice(
            ticker_id=stock.id,
            close=100.0,
            price_date=dt.now(timezone.utc) - timedelta(days=1),
            source="test",
        )
    )
    await db.commit()

    async def _session(symbol, window):
        return now_ending(_dip_and_rip_session())

    monkeypatch.setattr("app.routers.setups.fetch_intraday", _session)

    body = (
        await client.get(f"/setups?ticker={stock.ticker}&account_equity=20000")
    ).json()

    assert body["signals"], body
    signal = body["signals"][0]
    assert signal["direction"] == "long"
    assert signal["reward_risk"] >= setups.MIN_REWARD_RISK
    # Sized so a stop-out costs 0.5% of equity, not more.
    assert signal["position"]["actual_risk"] <= 100.0
    assert signal["position"]["shares"] > 0


@pytest.mark.asyncio
async def test_scan_can_explain_why_nothing_fired(client, seeded_stocks, monkeypatch):
    """Otherwise a quiet day is indistinguishable from a broken scanner."""

    async def _flat(symbol, window):
        return [bar(index * 5, 100.0) for index in range(20)]

    monkeypatch.setattr("app.routers.setups.fetch_intraday", _flat)

    body = (await client.get("/setups?ticker=MRNA&include_failed=true")).json()

    assert body["signals"] == []
    assert body["considered"], "no reasons given for a scan that found nothing"
    assert all(item["failed_checks"] for item in body["considered"])


@pytest.mark.asyncio
async def test_the_response_states_that_none_of_this_is_validated(client, seeded_stocks, monkeypatch):
    """A list of signals otherwise implies a track record that does not exist."""

    async def _none(symbol, window):
        return []

    monkeypatch.setattr("app.routers.setups.fetch_intraday", _none)

    body = (await client.get("/setups?ticker=MRNA")).json()

    assert "unmeasured" in body["caveat"]


@pytest.mark.asyncio
async def test_size_endpoint_matches_the_plans_worked_example(client):
    body = (
        await client.get("/setups/size?account_equity=20000&entry=120.20&stop=119.70")
    ).json()

    assert body["shares"] == 200


@pytest.mark.asyncio
async def test_the_prior_close_excludes_the_session_being_scanned(
    client, db, seeded_stocks, monkeypatch
):
    """Otherwise "up on the day" compares today against today.

    Once a backfill has run, the most recent stored daily close *is* today's,
    so taking it made L1 unable to trigger — and the failure was silent: no
    signals, indistinguishable from a quiet market.
    """
    from datetime import datetime as dt

    from app.models import StockPrice

    stock = seeded_stocks[0]
    session = now_ending(_dip_and_rip_session())
    session_day = session[-1].at

    db.add_all(
        [
            # Yesterday's close, which is the one the setup wants.
            StockPrice(
                ticker_id=stock.id,
                close=100.0,
                price_date=session_day - timedelta(days=1),
                source="test",
            ),
            # Today's, already written by the daily job. Using this would make
            # the comparison meaningless.
            StockPrice(
                ticker_id=stock.id,
                close=session[-1].close,
                price_date=session_day,
                source="test",
            ),
        ]
    )
    await db.commit()

    async def _session(symbol, window):
        return session

    monkeypatch.setattr("app.routers.setups.fetch_intraday", _session)

    body = (await client.get(f"/setups?ticker={stock.ticker}")).json()

    assert body["signals"], "the prior close was taken from the session being scanned"


def test_each_setup_runs_as_early_as_its_own_indicators_allow():
    """A round-number floor rejected setups the plan expects to be live.

    The plan runs L2 and S1 from 10:00 ET — thirty minutes after the open, six
    five-minute bars — but a flat 12-bar minimum delayed everything to 10:30.
    The floor is now whatever each setup's indicators need, so this pins that
    those needs are actually met at the stated minimum rather than the number
    merely being smaller.
    """
    for key, evaluate in setups.SETUPS.items():
        minimum = setups.MIN_BARS[key]
        session = [bar(index * 5, 100.0 + index * 0.1) for index in range(minimum)]

        evaluation = evaluate("MU", session, 100.0)

        assert "enough bars" not in evaluation.failed, (
            f"{key} needs more than the {minimum} bars MIN_BARS claims"
        )


def test_the_bar_shortfall_says_how_many_are_needed():
    """"11 bars" alone does not tell you whether to wait or to give up."""
    short = [bar(index * 5, 100.0) for index in range(3)]

    evaluation = setups.vwap_bounce("MU", short)

    detail = next(c.detail for c in evaluation.checks if c.name == "enough bars")
    assert str(setups.MIN_BARS["L2"]) in detail


# --- The board ---------------------------------------------------------------


@pytest.mark.asyncio
async def test_the_board_ranks_by_how_close_each_row_came(client, seeded_stocks, monkeypatch):
    """"Which of these is one condition away" should be answerable by looking."""
    strong = now_ending(_dip_and_rip_session())
    weak = now_ending([bar(index * 5, 100.0 - index) for index in range(12)])

    async def _bars(symbol, window):
        return strong if symbol == seeded_stocks[0].ticker else weak

    monkeypatch.setattr("app.routers.setups.fetch_intraday", _bars)

    body = (await client.get("/setups/board?setup=L1")).json()

    assert body["rows"], body
    assert body["rows"][0]["passed"] >= body["rows"][-1]["passed"]
    for row in body["rows"]:
        assert row["marks"].count("+") == row["passed"]
        assert len(row["marks"]) == row["total"]


@pytest.mark.asyncio
async def test_a_closed_market_is_marked_not_scored(client, seeded_stocks, monkeypatch):
    """Most of this universe is listed outside the US.

    Those markets are shut during the US session, so their newest bar is
    yesterday's close. Scoring it produces a signal nobody can act on.
    """

    async def _yesterday(symbol, window):
        return _dip_and_rip_session()  # fixed timestamps, hours old

    monkeypatch.setattr("app.routers.setups.fetch_intraday", _yesterday)

    body = (await client.get("/setups/board")).json()
    assert body["rows"] == []
    assert body["stale_markets"] >= 1

    included = (await client.get("/setups/board?include_stale=true")).json()
    assert included["rows"]
    assert all(row["live"] is False for row in included["rows"])


@pytest.mark.asyncio
async def test_the_scan_skips_closed_markets_by_default(client, db, seeded_stocks, monkeypatch):
    """A group like data_storage spans Seoul, Taipei and New York."""

    async def _yesterday(symbol, window):
        return _dip_and_rip_session()

    monkeypatch.setattr("app.routers.setups.fetch_intraday", _yesterday)

    body = (await client.get("/setups?ticker=MRNA&ticker=PFE")).json()

    assert body["signals"] == []
    assert sorted(body["stale_markets"]) == ["MRNA", "PFE"]
    assert body["scanned"] == 0


@pytest.mark.asyncio
async def test_min_passed_filters_the_board_to_near_misses(client, seeded_stocks, monkeypatch):
    async def _bars(symbol, window):
        return now_ending([bar(index * 5, 100.0 - index) for index in range(12)])

    monkeypatch.setattr("app.routers.setups.fetch_intraday", _bars)

    body = (await client.get("/setups/board?min_passed=99")).json()

    assert body["rows"] == []
    # The scan still happened; the filter is on presentation, not on work.
    assert body["scanned"] >= 1
