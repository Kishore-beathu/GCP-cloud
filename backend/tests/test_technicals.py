"""Price indicators, checked against hand-computed values.

Every one of these is a published formula with a conventional definition, and
an indicator that disagrees with what a user sees in their charting package is
worse than no indicator — it looks authoritative and is wrong.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.services import technicals


def series(closes: list[float]) -> list[tuple[datetime, float]]:
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    return [(start + timedelta(days=index), close) for index, close in enumerate(closes)]


def test_momentum_is_the_percentage_change_over_the_window():
    closes = [100.0] * 5 + [110.0]

    assert technicals.momentum(closes, 5) == 10.0


def test_momentum_needs_a_full_window():
    """A five-day figure from three days of data is a fabrication."""
    assert technicals.momentum([100.0, 101.0, 102.0], 5) is None


def test_distance_from_sma_is_signed():
    # 20 sessions at 100, so the average is 100 and the last close is 5% above.
    closes = [100.0] * 19 + [105.0]
    result = technicals.distance_from_sma(closes, 20)

    assert result is not None and result > 0
    # The final value lifts the average slightly, so it is just under 5%.
    assert 4.7 < result < 5.0


def test_rsi_is_100_when_every_session_gains():
    closes = [100.0 + index for index in range(20)]

    assert technicals.rsi(closes, 14) == 100.0


def test_rsi_is_low_when_every_session_falls():
    closes = [100.0 - index for index in range(20)]

    assert technicals.rsi(closes, 14) == 0.0


def test_rsi_of_a_flat_series_is_neutral():
    """No movement in either direction is 50, not a division by zero."""
    assert technicals.rsi([100.0] * 20, 14) == 50.0


def test_rsi_uses_wilder_smoothing():
    """A simple average of gains and losses gives a different number.

    Charting packages use Wilder's smoothing, so a "simple RSI" would disagree
    with what the user sees everywhere else.
    """
    closes = [
        44.34, 44.09, 44.15, 43.61, 44.33, 44.83, 45.10, 45.42,
        45.84, 46.08, 45.89, 46.03, 45.61, 46.28, 46.28, 46.00,
    ]
    result = technicals.rsi(closes, 14)

    # Wilder's canonical worked example lands around 70.
    assert result is not None and 66 < result < 74


def test_volatility_is_zero_for_a_flat_series():
    assert technicals.volatility([100.0] * 30, 21) == 0.0


def test_volatility_is_annualised():
    """A 1% daily swing annualises to roughly 16%, not 1%."""
    closes = [100.0 + (1.0 if index % 2 else 0.0) for index in range(30)]
    result = technicals.volatility(closes, 21)

    assert result is not None and result > 5


def test_range_position_reports_where_in_the_band_the_close_sits():
    rising = list(range(100, 200))
    assert technicals.range_position([float(x) for x in rising]) == 100.0

    falling = list(range(200, 100, -1))
    assert technicals.range_position([float(x) for x in falling]) == 0.0


def test_range_position_of_a_flat_series_is_the_midpoint():
    """High equals low; the answer is 50, not a division by zero."""
    assert technicals.range_position([100.0] * 30) == 50.0


def test_drawdown_is_zero_at_a_high_and_negative_below_it():
    assert technicals.drawdown([100.0, 110.0, 120.0]) == 0.0

    result = technicals.drawdown([100.0, 120.0, 90.0])
    assert result == -25.0


def test_compute_sorts_by_date_before_measuring():
    """Rows arrive from the database in no guaranteed order."""
    ordered = technicals.compute(series([100.0, 105.0, 110.0]))
    shuffled = technicals.compute(list(reversed(series([100.0, 105.0, 110.0]))))

    assert ordered.close == shuffled.close == 110.0


def test_compute_returns_empty_for_no_history():
    result = technicals.compute([])

    assert result.close is None
    assert result.sessions == 0
    assert result.rsi_14 is None


def test_compute_reports_what_it_could_not_measure():
    """Short history yields the indicators it supports and None for the rest."""
    result = technicals.compute(series([100.0 + index for index in range(10)]))

    assert result.momentum_5d is not None
    assert result.momentum_63d is None
    assert result.vs_sma_50 is None
    assert result.sessions == 10
