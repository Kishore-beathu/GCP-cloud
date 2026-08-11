"""Intraday indicators, against sessions written by hand.

Every one of these is a claim about what happened inside a session, which a
daily close cannot express — so they are tested against bars built to contain a
known answer rather than against whatever the market did.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.integrations.yahoo import Bar
from app.services import intraday

OPEN = datetime(2026, 8, 11, 13, 30, tzinfo=timezone.utc)  # 09:30 ET


def bar(minute: int, close: float, **kwargs) -> Bar:
    """One 5-minute bar, defaulting to a tight body around the close."""
    return Bar(
        at=OPEN + timedelta(minutes=minute),
        close=close,
        open=kwargs.get("open", close),
        high=kwargs.get("high", close),
        low=kwargs.get("low", close),
        volume=kwargs.get("volume", 1000),
    )


# --- VWAP --------------------------------------------------------------------


def test_vwap_weights_by_volume_not_by_bar_count():
    """The whole point of VWAP: one huge print outweighs several small ones."""
    bars = [
        bar(0, 100.0, volume=100),
        bar(5, 200.0, volume=900),
    ]

    # A plain mean would say 150; the volume says otherwise.
    assert intraday.vwap(bars) == 190.0


def test_vwap_is_none_without_volume():
    """A mean of prices is not a VWAP, whatever it is called.

    Returning the unweighted average here would answer a different question
    under the same name — the mistake range_position used to make with its
    52-week window.
    """
    bars = [Bar(at=OPEN, close=100.0), Bar(at=OPEN + timedelta(minutes=5), close=200.0)]

    assert intraday.vwap(bars) is None


def test_vwap_uses_the_typical_price_when_the_bar_has_a_range():
    bars = [bar(0, 100.0, high=110.0, low=90.0, volume=10)]

    assert intraday.vwap(bars) == 100.0  # (110 + 90 + 100) / 3


# --- EMA ---------------------------------------------------------------------


def test_ema_needs_a_full_window():
    assert intraday.ema([1.0, 2.0], 9) is None


def test_ema_tracks_the_recent_values_more_closely_than_a_mean():
    values = [10.0] * 9 + [20.0] * 5
    average = sum(values) / len(values)

    result = intraday.ema(values, 9)

    assert result is not None and result > average


# --- Opening range -----------------------------------------------------------


def test_opening_range_is_measured_in_minutes_not_bars():
    """So it means the same thing on 1-minute and 5-minute series."""
    bars = [bar(minute, 100.0 + minute, high=101.0 + minute, low=99.0 + minute)
            for minute in range(0, 60, 5)]

    high, low = intraday.opening_range(bars, minutes=15)

    # Bars at 0, 5 and 10 minutes only.
    assert high == 111.0
    assert low == 99.0


# --- Relative volume ---------------------------------------------------------


def test_relative_volume_compares_the_last_bar_to_the_run_before_it():
    bars = [bar(index * 5, 100.0, volume=1000) for index in range(5)]
    bars.append(bar(25, 100.0, volume=2000))

    assert intraday.relative_volume(bars, lookback=5) == 2.0


def test_relative_volume_is_none_without_enough_history():
    assert intraday.relative_volume([bar(0, 100.0)], lookback=5) is None


# --- Candle shape ------------------------------------------------------------


def test_upper_wick_share_measures_rejection_as_a_proportion():
    """Comparable across a $5 stock and a $500 one."""
    rejected = bar(0, 100.0, open=99.0, high=110.0, low=98.0)

    # Body tops at 100, high at 110, range 98-110 = 12.
    assert intraday.upper_wick_share(rejected) == round(10 / 12, 4)


def test_upper_wick_share_is_none_on_a_zero_range_bar():
    assert intraday.upper_wick_share(bar(0, 100.0, high=100.0, low=100.0)) is None


# --- Swings ------------------------------------------------------------------


def test_a_swing_high_needs_bars_on_both_sides():
    """A high with nothing after it has not been rejected yet.

    Treating the newest bar as a swing would put a short's stop inside the move
    it is supposed to survive.
    """
    rising = [bar(index * 5, 100.0 + index) for index in range(8)]

    assert intraday.swing_high(rising, lookback=3) is None


def test_swing_high_finds_the_peak_it_is_given():
    prices = [100.0, 101.0, 102.0, 105.0, 102.0, 101.0, 100.0, 99.0]
    bars = [bar(index * 5, price) for index, price in enumerate(prices)]

    high = intraday.swing_high(bars, lookback=3)

    assert high is not None and high.price == 105.0


def test_swing_low_finds_the_trough():
    prices = [105.0, 104.0, 103.0, 100.0, 103.0, 104.0, 105.0, 106.0]
    bars = [bar(index * 5, price) for index, price in enumerate(prices)]

    low = intraday.swing_low(bars, lookback=3)

    assert low is not None and low.price == 100.0


# --- Session boundaries ------------------------------------------------------


def test_session_bars_keeps_only_the_latest_day():
    """VWAP and the opening range reset at the open.

    A 5-day window carries several sessions; measuring across all of them would
    anchor today's setup to last Tuesday.
    """
    yesterday = [
        Bar(at=OPEN - timedelta(days=1) + timedelta(minutes=index * 5), close=90.0)
        for index in range(5)
    ]
    today = [bar(index * 5, 100.0) for index in range(5)]

    kept = intraday.session_bars(yesterday + today)

    assert len(kept) == 5
    assert all(item.close == 100.0 for item in kept)
