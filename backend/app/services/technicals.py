"""Price-derived indicators, computed from the daily closes already stored.

The platform could say what the news meant and not whether the price agreed.
These are the standard measures a discretionary reader would glance at before
acting on a headline — trend, momentum, where the price sits in its own range,
and how violent that range has been.

Everything here is a pure function over a closing series so it can be tested
against hand-checked numbers rather than against a database. Each returns
``None`` rather than a fabricated value when there is not enough history: a
momentum figure computed from four days of data is worse than no figure,
because it looks equally authoritative.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class Technicals:
    """Indicators for one symbol, any of which may be unavailable."""

    close: float | None = None
    # Percentage change over the trailing window, in percent.
    momentum_5d: float | None = None
    momentum_21d: float | None = None
    momentum_63d: float | None = None
    # Close relative to its own moving average, in percent. Positive is above.
    vs_sma_20: float | None = None
    vs_sma_50: float | None = None
    # 0-100. Wilder's relative strength index over 14 sessions.
    rsi_14: float | None = None
    # Annualised standard deviation of daily returns, in percent.
    volatility_21d: float | None = None
    # Where the close sits in the trailing 52-week range, 0-100.
    range_position_52w: float | None = None
    # Decline from the highest close in the window, in percent (negative).
    drawdown_52w: float | None = None
    sessions: int = 0

    def as_dict(self) -> dict:
        return {
            "close": self.close,
            "momentum_5d": self.momentum_5d,
            "momentum_21d": self.momentum_21d,
            "momentum_63d": self.momentum_63d,
            "vs_sma_20": self.vs_sma_20,
            "vs_sma_50": self.vs_sma_50,
            "rsi_14": self.rsi_14,
            "volatility_21d": self.volatility_21d,
            "range_position_52w": self.range_position_52w,
            "drawdown_52w": self.drawdown_52w,
            "sessions": self.sessions,
        }


def _pct_change(newer: float, older: float) -> float | None:
    if not older:
        return None
    return round((newer - older) / older * 100, 4)


def momentum(closes: list[float], window: int) -> float | None:
    """Percentage change over the last ``window`` sessions."""
    if len(closes) < window + 1:
        return None
    return _pct_change(closes[-1], closes[-1 - window])


def sma(closes: list[float], window: int) -> float | None:
    if len(closes) < window:
        return None
    return sum(closes[-window:]) / window


def distance_from_sma(closes: list[float], window: int) -> float | None:
    """How far the last close sits above or below its moving average, in percent."""
    average = sma(closes, window)
    if average is None or not average:
        return None
    return _pct_change(closes[-1], average)


def rsi(closes: list[float], window: int = 14) -> float | None:
    """Wilder's RSI: the share of recent movement that has been upward.

    Uses Wilder's smoothing rather than a simple average of gains and losses,
    which is the convention every charting package follows — a "simple RSI"
    produces different numbers and would not agree with what a user sees
    elsewhere.
    """
    if len(closes) < window + 1:
        return None

    gains: list[float] = []
    losses: list[float] = []
    for previous, current in zip(closes, closes[1:]):
        change = current - previous
        gains.append(max(change, 0.0))
        losses.append(max(-change, 0.0))

    average_gain = sum(gains[:window]) / window
    average_loss = sum(losses[:window]) / window
    for gain, loss in zip(gains[window:], losses[window:]):
        average_gain = (average_gain * (window - 1) + gain) / window
        average_loss = (average_loss * (window - 1) + loss) / window

    if average_loss == 0:
        # Unbroken gains. RSI is defined as 100 here, not undefined.
        return 100.0 if average_gain else 50.0
    strength = average_gain / average_loss
    return round(100 - (100 / (1 + strength)), 4)


def volatility(closes: list[float], window: int = 21) -> float | None:
    """Annualised standard deviation of daily returns, in percent."""
    if len(closes) < window + 1:
        return None

    returns = [
        (current - previous) / previous
        for previous, current in zip(closes[-window - 1 :], closes[-window:])
        if previous
    ]
    if len(returns) < 2:
        return None

    mean = sum(returns) / len(returns)
    variance = sum((value - mean) ** 2 for value in returns) / (len(returns) - 1)
    # 252 trading sessions a year is the standard convention.
    return round(variance**0.5 * (252**0.5) * 100, 4)


def range_position(closes: list[float], window: int = 252) -> float | None:
    """Where the last close sits between the window's low and high, 0-100.

    Requires the whole window. Slicing ``closes[-252:]`` off a shorter series
    silently answers a different question and reports it under the same name:
    a symbol with three months of history scored near 100 on "position in the
    52-week range" when the range measured was two months long, and was then
    ranked against symbols whose figure genuinely spanned a year. Returning
    None costs the factor and says so through `coverage`, which is the honest
    trade.
    """
    if len(closes) < window:
        return None
    recent = closes[-window:]
    low, high = min(recent), max(recent)
    if high == low:
        return 50.0
    return round((closes[-1] - low) / (high - low) * 100, 4)


def drawdown(closes: list[float], window: int = 252) -> float | None:
    """Decline from the window's highest close, in percent (zero or negative).

    Requires the whole window, for the same reason as `range_position`: a
    drawdown from a two-month peak is not a drawdown from a 52-week peak, and
    reporting one as the other flatters every recently added symbol.
    """
    if len(closes) < window:
        return None
    recent = closes[-window:]
    peak = max(recent)
    if not peak:
        return None
    return round((closes[-1] - peak) / peak * 100, 4)


def compute(series: list[tuple[datetime, float]]) -> Technicals:
    """Every indicator for one symbol, from a date-ordered closing series."""
    ordered = [close for _, close in sorted(series, key=lambda row: row[0])]
    if not ordered:
        return Technicals()

    return Technicals(
        close=ordered[-1],
        momentum_5d=momentum(ordered, 5),
        momentum_21d=momentum(ordered, 21),
        momentum_63d=momentum(ordered, 63),
        vs_sma_20=distance_from_sma(ordered, 20),
        vs_sma_50=distance_from_sma(ordered, 50),
        rsi_14=rsi(ordered, 14),
        volatility_21d=volatility(ordered, 21),
        range_position_52w=range_position(ordered, 252),
        drawdown_52w=drawdown(ordered, 252),
        sessions=len(ordered),
    )
