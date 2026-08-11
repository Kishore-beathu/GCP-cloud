"""Intraday indicators: the vocabulary the day-trading setups are written in.

The daily technicals in `technicals.py` cannot express these. "Pulls back to
VWAP and holds", "volume 1.5x the recent average", "long upper wick into
resistance" are all statements about what happened *inside* a session, and a
daily close has thrown that away by definition.

Two things every function here shares:

* **They return None rather than a number they cannot support.** A VWAP over
  bars with no volume is not a VWAP, and an opening range needs the opening
  bars to exist. A setup that reads None simply does not trigger, which is the
  correct outcome — the alternative is a signal built on a fabricated level.
* **They take bars, not a symbol.** No I/O, so the setups are testable against
  a hand-written session rather than against whatever the market did today.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from app.integrations.yahoo import Bar


@dataclass(frozen=True)
class Swing:
    """A local high or low, and where it sat."""

    at: datetime
    price: float


def vwap(bars: list[Bar]) -> float | None:
    """Volume-weighted average price over the bars given.

    The reference every one of these setups is anchored to: it is where the
    session's volume actually traded, so "above VWAP" means "buyers are paying
    more than the average participant did today" rather than an arbitrary line.

    Requires volume. Falling back to a plain average when volume is missing
    would return a number under the same name that answers a different
    question, which is the mistake `range_position` used to make.
    """
    total_volume = 0
    total_value = 0.0
    for bar in bars:
        if bar.volume is None or bar.volume <= 0:
            continue
        typical = _typical_price(bar)
        total_value += typical * bar.volume
        total_volume += bar.volume
    if not total_volume:
        return None
    return round(total_value / total_volume, 6)


def _typical_price(bar: Bar) -> float:
    """(high + low + close) / 3, the conventional VWAP input.

    Falls back to the close when the bar has no high or low: a bar that only
    reports a close still traded there, and using it is closer to the truth
    than dropping the bar's volume from the average entirely.
    """
    if bar.high is None or bar.low is None:
        return bar.close
    return (bar.high + bar.low + bar.close) / 3


def ema(values: list[float], window: int) -> float | None:
    """Exponential moving average, seeded with the simple average.

    Seeding with the first value instead would leave the average dominated by
    one bar for the first ``window`` periods — on a 9-EMA over 5-minute bars
    that is the whole first 45 minutes, which is exactly when these setups
    read it.
    """
    if len(values) < window or window < 1:
        return None
    average = sum(values[:window]) / window
    multiplier = 2 / (window + 1)
    for value in values[window:]:
        average = (value - average) * multiplier + average
    return round(average, 6)


def opening_range(bars: list[Bar], minutes: int = 15) -> tuple[float, float] | None:
    """High and low of the session's first ``minutes``.

    The level the gap-and-go setup breaks out of. Built from the bars' own
    timestamps rather than a bar count, so it means the same thing whether the
    series is 1-minute or 5-minute bars.
    """
    if not bars:
        return None
    start = bars[0].at
    opening = [bar for bar in bars if (bar.at - start).total_seconds() < minutes * 60]
    if not opening:
        return None

    highs = [bar.high for bar in opening if bar.high is not None] or [
        bar.close for bar in opening
    ]
    lows = [bar.low for bar in opening if bar.low is not None] or [
        bar.close for bar in opening
    ]
    return max(highs), min(lows)


def relative_volume(bars: list[Bar], lookback: int = 5) -> float | None:
    """The last bar's volume as a multiple of the previous ``lookback`` bars.

    "Volume >= 1.5x the average of the last few bars" is the confirmation on
    every entry in the plan, and it is what separates a breakout from a drift
    through a level.
    """
    volumes = [bar.volume for bar in bars if bar.volume is not None]
    if len(volumes) < lookback + 1:
        return None
    recent = volumes[-lookback - 1 : -1]
    average = sum(recent) / len(recent)
    if not average:
        return None
    return round(volumes[-1] / average, 4)


def upper_wick_share(bar: Bar) -> float | None:
    """What share of the bar's range sits above the body, 0-1.

    A long upper wick is the plan's exhaustion tell: price went there and was
    rejected. Expressed as a share so it is comparable across a $5 stock and a
    $500 one.
    """
    if bar.high is None or bar.low is None or bar.open is None:
        return None
    span = bar.high - bar.low
    if span <= 0:
        return None
    return round((bar.high - max(bar.open, bar.close)) / span, 4)


def is_bullish(bar: Bar) -> bool | None:
    """Whether the bar closed above its open."""
    if bar.open is None:
        return None
    return bar.close > bar.open


def swing_high(bars: list[Bar], lookback: int = 3) -> Swing | None:
    """The most recent bar higher than ``lookback`` bars either side of it.

    Where a short's stop goes. Requires bars on *both* sides, so the most
    recent bars cannot form a swing — a high with nothing after it has not
    been rejected yet, and treating it as one would place the stop inside the
    move it is meant to survive.
    """
    return _swing(bars, lookback, high=True)


def swing_low(bars: list[Bar], lookback: int = 3) -> Swing | None:
    """The most recent confirmed local low. Where a long's stop goes."""
    return _swing(bars, lookback, high=False)


def _swing(bars: list[Bar], lookback: int, high: bool) -> Swing | None:
    def level(bar: Bar) -> float:
        if high:
            return bar.high if bar.high is not None else bar.close
        return bar.low if bar.low is not None else bar.close

    for index in range(len(bars) - lookback - 1, lookback - 1, -1):
        pivot = level(bars[index])
        neighbours = [
            level(bars[other])
            for other in range(index - lookback, index + lookback + 1)
            if other != index
        ]
        if high and all(pivot > other for other in neighbours):
            return Swing(at=bars[index].at, price=pivot)
        if not high and all(pivot < other for other in neighbours):
            return Swing(at=bars[index].at, price=pivot)
    return None


def session_bars(bars: list[Bar]) -> list[Bar]:
    """Only the bars from the most recent trading date.

    A 5-day window carries several sessions, and VWAP, the opening range and
    the day's high all reset at the open. Computing them across the whole
    window would anchor today's setup to last Tuesday.
    """
    if not bars:
        return []
    last_date = bars[-1].at.date()
    return [bar for bar in bars if bar.at.date() == last_date]


def lowest(bars: list[Bar]) -> float | None:
    """Lowest traded price across the bars given."""
    lows = [bar.low if bar.low is not None else bar.close for bar in bars]
    return min(lows) if lows else None


def highest(bars: list[Bar]) -> float | None:
    """Highest traded price across the bars given."""
    highs = [bar.high if bar.high is not None else bar.close for bar in bars]
    return max(highs) if highs else None


def makes_higher_lows(bars: list[Bar]) -> bool:
    """Whether the second half of the series troughed above the first half.

    A working definition of "uptrend" that does not need confirmed swings.
    Comparing two `swing_low` results cannot describe the present: a swing
    needs bars on both sides, so the most recent leg of a trend is invisible
    to it — which is exactly the leg you are trading.
    """
    if len(bars) < 6:
        return False
    middle = len(bars) // 2
    first, second = lowest(bars[:middle]), lowest(bars[middle:])
    return first is not None and second is not None and second > first
