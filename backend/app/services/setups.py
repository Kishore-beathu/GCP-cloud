"""The four intraday setups, as checkable conditions rather than prose.

Each setup is a checklist, and a checklist that only reports pass/fail is
useless for deciding whether to take a trade — you need to see which condition
failed. Every evaluation therefore returns the whole checklist, and a signal
only when all of it passes.

Three things this module deliberately does not do:

* **It does not claim these work.** The setups are widely described and were
  supplied as a plan; that is not evidence. Nothing here has been validated,
  and — see below — it cannot be on this platform's stored data.
* **It does not size a position from a guess.** Entry, stop and target are
  taken from levels on the chart. If a level is missing, the setup does not
  trigger rather than inventing one.
* **It does not decide for you.** A signal is "these conditions are true now",
  not an instruction.

**On validating these.** `stock_prices` holds one row per trading day by
design, and intraday bars are fetched live and never stored. So none of these
can be run through `scoring.validate()` — there is no stored history of what a
5-minute chart looked like at 09:47 last Tuesday. Until intraday bars are
stored, these are a live scanner, and their hit rate is unknown. Saying so is
the point: the alternative is a number nobody measured.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from app.integrations.yahoo import Bar
from app.services import intraday

# From the plan's risk rules. These are the caller's to override, but the
# defaults encode the "low risk" brief: half a percent at stake, and no trade
# taken unless the level structure offers twice that in reward.
DEFAULT_RISK_FRACTION = 0.005
MIN_REWARD_RISK = 2.0

# "Volume >= 1.5x the average of the last few bars" — the confirmation on every
# entry in the plan.
VOLUME_CONFIRMATION = 1.5

# A wick this share of the bar's range counts as a rejection.
EXHAUSTION_WICK = 0.5

# How many recent bars count as "the attempt" on a level. The resistance a
# failed breakout fails at is what the session established before them.
ATTEMPT_BARS = 6

# Each setup's minimum session length, derived from what its indicators need
# rather than picked as a round number. A round 12 delayed every setup to an
# hour after the open, while the plan runs L2 and S1 from 10:00 ET — thirty
# minutes in, six bars — so the floor was rejecting setups the specification
# expected to be live.
#
#   L1  an opening range, a pullback, and a bar reclaiming it.
#   L2  the 9-EMA is binding; the pullback window needs four bars after it.
#   S1  ATTEMPT_BARS of attempt, plus enough before them to establish a level.
#   S2  a swing high needs 2*lookback+1 bars, plus bars after it to roll over.
MIN_BARS = {"L1": 6, "L2": 9, "S1": 10, "S2": 10}


@dataclass(frozen=True)
class Check:
    """One condition, and whether it held."""

    name: str
    passed: bool
    detail: str

    def as_dict(self) -> dict:
        return {"name": self.name, "passed": self.passed, "detail": self.detail}


@dataclass
class Signal:
    """A setup that fired, with the levels it fired at."""

    setup: str
    ticker: str
    direction: str  # "long" or "short"
    entry: float
    stop: float
    target: float
    at: datetime
    checks: list[Check] = field(default_factory=list)

    @property
    def risk_per_share(self) -> float:
        return abs(self.entry - self.stop)

    @property
    def reward_risk(self) -> float | None:
        risk = self.risk_per_share
        if not risk:
            return None
        return round(abs(self.target - self.entry) / risk, 2)

    def as_dict(self) -> dict:
        return {
            "setup": self.setup,
            "ticker": self.ticker,
            "direction": self.direction,
            "entry": round(self.entry, 4),
            "stop": round(self.stop, 4),
            "target": round(self.target, 4),
            "risk_per_share": round(self.risk_per_share, 4),
            "reward_risk": self.reward_risk,
            "at": self.at.isoformat(),
            "checks": [check.as_dict() for check in self.checks],
        }


@dataclass
class Evaluation:
    """What one setup made of one symbol, whether or not it fired."""

    setup: str
    ticker: str
    signal: Signal | None
    checks: list[Check]

    @property
    def failed(self) -> list[str]:
        return [check.name for check in self.checks if not check.passed]

    def as_dict(self) -> dict:
        return {
            "setup": self.setup,
            "ticker": self.ticker,
            "triggered": self.signal is not None,
            "failed_checks": self.failed,
            "checks": [check.as_dict() for check in self.checks],
            "signal": self.signal.as_dict() if self.signal else None,
        }


def position_size(
    account_equity: float, entry: float, stop: float, risk_fraction: float = DEFAULT_RISK_FRACTION
) -> dict:
    """Shares to trade so that being stopped out costs exactly the risk budget.

    This is the whole of "low risk" in practice: the stop distance decides the
    size, so a wide stop buys fewer shares and every trade puts the same amount
    at stake regardless of the setup or the share price.
    """
    risk_per_share = abs(entry - stop)
    budget = account_equity * risk_fraction
    if risk_per_share <= 0:
        return {"shares": 0, "risk_budget": round(budget, 2), "detail": "stop equals entry"}
    shares = int(budget // risk_per_share)
    return {
        "shares": shares,
        "risk_per_share": round(risk_per_share, 4),
        "risk_budget": round(budget, 2),
        "capital_required": round(shares * entry, 2),
        "actual_risk": round(shares * risk_per_share, 2),
    }


def _check(name: str, passed: bool, detail: str) -> Check:
    return Check(name=name, passed=passed, detail=detail)


def _finish(
    setup: str,
    ticker: str,
    checks: list[Check],
    *,
    direction: str,
    entry: float | None,
    stop: float | None,
    target: float | None,
    at: datetime,
) -> Evaluation:
    """Build the evaluation, refusing the signal unless everything held.

    The reward:risk floor is applied here rather than inside each setup so no
    setup can quietly ship a trade that does not clear it.
    """
    if not all(check.passed for check in checks) or entry is None or stop is None:
        return Evaluation(setup=setup, ticker=ticker, signal=None, checks=checks)

    risk = abs(entry - stop)
    if risk <= 0:
        checks.append(_check("risk defined", False, "stop is at the entry price"))
        return Evaluation(setup=setup, ticker=ticker, signal=None, checks=checks)

    if target is None:
        target = entry + risk * MIN_REWARD_RISK * (1 if direction == "long" else -1)

    reward_risk = abs(target - entry) / risk
    ok = reward_risk >= MIN_REWARD_RISK
    checks.append(
        _check(
            f"reward:risk >= {MIN_REWARD_RISK}",
            ok,
            f"target offers {reward_risk:.2f}R",
        )
    )
    if not ok:
        return Evaluation(setup=setup, ticker=ticker, signal=None, checks=checks)

    return Evaluation(
        setup=setup,
        ticker=ticker,
        signal=Signal(
            setup=setup,
            ticker=ticker,
            direction=direction,
            entry=entry,
            stop=stop,
            target=target,
            at=at,
            checks=checks,
        ),
        checks=checks,
    )


# --- L1: dip and rip ---------------------------------------------------------


def dip_and_rip(
    ticker: str, bars: list[Bar], previous_close: float | None
) -> Evaluation:
    """Momentum continuation after the open holds above VWAP.

    The plan's first long: up on the day, a pullback that holds above VWAP and
    above yesterday's close, then a bar closing back above the opening range on
    volume.
    """
    setup = "L1 dip and rip"
    session = intraday.session_bars(bars)
    checks: list[Check] = []

    if len(session) < MIN_BARS["L1"] or previous_close is None:
        checks.append(
            _check(
                "enough bars",
                False,
                f"{len(session)} bars this session, needs {MIN_BARS['L1']}"
                + ("" if previous_close is not None else "; no stored prior close"),
            )
        )
        return Evaluation(setup=setup, ticker=ticker, signal=None, checks=checks)

    last = session[-1]
    session_vwap = intraday.vwap(session)
    opening = intraday.opening_range(session)
    rel_volume = intraday.relative_volume(session)

    checks.append(
        _check(
            "VWAP available",
            session_vwap is not None,
            "no volume in the session bars" if session_vwap is None else f"VWAP {session_vwap:.2f}",
        )
    )
    checks.append(
        _check(
            "opening range formed",
            opening is not None,
            "no opening bars" if opening is None else f"range {opening[1]:.2f}-{opening[0]:.2f}",
        )
    )
    if session_vwap is None or opening is None:
        return Evaluation(setup=setup, ticker=ticker, signal=None, checks=checks)

    range_high, _ = opening
    checks.append(
        _check(
            "up on the day",
            last.close > previous_close,
            f"{last.close:.2f} vs prior close {previous_close:.2f}",
        )
    )
    checks.append(
        _check("holding above VWAP", last.close > session_vwap, f"close {last.close:.2f}")
    )
    checks.append(
        _check(
            "reclaimed the opening range",
            last.close > range_high,
            f"close {last.close:.2f} vs range high {range_high:.2f}",
        )
    )
    checks.append(
        _check(
            f"volume >= {VOLUME_CONFIRMATION}x recent",
            rel_volume is not None and rel_volume >= VOLUME_CONFIRMATION,
            "volume missing" if rel_volume is None else f"{rel_volume:.2f}x",
        )
    )

    # Stop under the pullback, or under VWAP — the plan says whichever is
    # tighter while still being a real level.
    low = intraday.swing_low(session)
    stop_candidates = [session_vwap] + ([low.price] if low else [])
    stop = max(candidate for candidate in stop_candidates if candidate < last.close) if any(
        candidate < last.close for candidate in stop_candidates
    ) else None
    checks.append(
        _check(
            "stop level exists",
            stop is not None,
            "no level below price to stop under" if stop is None else f"stop {stop:.2f}",
        )
    )

    return _finish(
        setup,
        ticker,
        checks,
        direction="long",
        entry=last.close,
        stop=stop,
        target=None,  # 2R by default; the plan's second target is discretionary
        at=last.at,
    )


# --- L2: VWAP bounce ---------------------------------------------------------


def vwap_bounce(ticker: str, bars: list[Bar], previous_close: float | None = None) -> Evaluation:
    """Buying the pullback to value inside an established intraday uptrend."""
    setup = "L2 VWAP bounce"
    session = intraday.session_bars(bars)
    checks: list[Check] = []

    if len(session) < MIN_BARS["L2"]:
        checks.append(
            _check(
                "enough bars",
                False,
                f"{len(session)} bars this session, needs {MIN_BARS['L2']}",
            )
        )
        return Evaluation(setup=setup, ticker=ticker, signal=None, checks=checks)

    last = session[-1]
    session_vwap = intraday.vwap(session)
    closes = [bar.close for bar in session]
    ema9 = intraday.ema(closes, 9)

    checks.append(
        _check(
            "VWAP available",
            session_vwap is not None,
            "no volume" if session_vwap is None else f"VWAP {session_vwap:.2f}",
        )
    )
    if session_vwap is None or ema9 is None:
        checks.append(_check("9 EMA available", ema9 is not None, "not enough bars"))
        return Evaluation(setup=setup, ticker=ticker, signal=None, checks=checks)

    # An uptrend, defined so it can describe the present. Comparing two
    # confirmed swing lows cannot: a swing needs bars on both sides, so the
    # most recent leg of a trend is invisible to it, and that is the leg being
    # traded.
    higher_low = intraday.makes_higher_lows(session)
    checks.append(
        _check(
            "uptrend (higher lows)",
            higher_low,
            "the session is not troughing higher" if not higher_low else "second half troughed higher",
        )
    )
    checks.append(_check("above the 9 EMA", last.close > ema9, f"EMA {ema9:.2f}"))

    # The pullback touched value and the last bar turned back up from it.
    touched = min(
        (bar.low if bar.low is not None else bar.close) for bar in session[-4:]
    ) <= session_vwap
    checks.append(
        _check("pulled back to VWAP", touched, f"VWAP {session_vwap:.2f}")
    )
    bullish = intraday.is_bullish(last)
    checks.append(
        _check(
            "bounce candle is bullish",
            bool(bullish),
            "last bar closed below its open" if bullish is False else "closed above its open",
        )
    )
    checks.append(_check("closed back above VWAP", last.close > session_vwap, f"{last.close:.2f}"))

    # Under the pullback low, not under a confirmed swing: at the moment a
    # bounce is bought the low is one bar back and the chart has not finished
    # making it. A trader stops under where the pullback actually turned.
    pullback_low = intraday.lowest(session[-4:])
    stop = pullback_low if pullback_low is not None and pullback_low < last.close else None
    checks.append(
        _check(
            "stop level exists",
            stop is not None,
            "no pullback low below price" if stop is None else f"stop {stop:.2f}",
        )
    )

    # Target the session high: the plan's "prior intraday high".
    highs = [bar.high if bar.high is not None else bar.close for bar in session]
    target = max(highs) if highs else None

    return _finish(
        setup, ticker, checks, direction="long", entry=last.close, stop=stop, target=target,
        at=last.at,
    )


# --- S1: failed breakout -----------------------------------------------------


def failed_breakout(
    ticker: str, bars: list[Bar], previous_close: float | None = None
) -> Evaluation:
    """Shorting a level that was taken and immediately given back."""
    setup = "S1 failed breakout"
    session = intraday.session_bars(bars)
    checks: list[Check] = []

    if len(session) < MIN_BARS["S1"]:
        checks.append(
            _check(
                "enough bars",
                False,
                f"{len(session)} bars this session, needs {MIN_BARS['S1']}",
            )
        )
        return Evaluation(setup=setup, ticker=ticker, signal=None, checks=checks)

    last = session[-1]
    session_vwap = intraday.vwap(session)

    # The resistance is whatever the session had established *before* the
    # attempt, so the recent bars are excluded from it. Taking the most recent
    # swing high instead makes the poke itself the level, and then asks
    # whether the break was broken — which nothing ever satisfies.
    established, recent = session[:-ATTEMPT_BARS], session[-ATTEMPT_BARS:]
    resistance = intraday.highest(established)

    checks.append(
        _check(
            "resistance identified",
            resistance is not None,
            "not enough session before the attempt"
            if resistance is None
            else f"resistance {resistance:.2f}",
        )
    )
    checks.append(
        _check(
            "VWAP available",
            session_vwap is not None,
            "no volume" if session_vwap is None else f"VWAP {session_vwap:.2f}",
        )
    )
    if resistance is None or session_vwap is None:
        return Evaluation(setup=setup, ticker=ticker, signal=None, checks=checks)

    poked = any(
        (bar.high if bar.high is not None else bar.close) > resistance for bar in recent
    )
    checks.append(
        _check(
            "level was broken",
            poked,
            f"traded above {resistance:.2f}" if poked else f"never traded above {resistance:.2f}",
        )
    )
    checks.append(
        _check(
            "closed back below the level",
            last.close < resistance,
            f"close {last.close:.2f} vs {resistance:.2f}",
        )
    )
    bearish = intraday.is_bullish(last)
    checks.append(
        _check(
            "rejection bar is bearish",
            bearish is False,
            "closed below its open" if bearish is False else "closed above its open",
        )
    )
    rel_volume = intraday.relative_volume(session)
    checks.append(
        _check(
            f"volume >= {VOLUME_CONFIRMATION}x recent",
            rel_volume is not None and rel_volume >= VOLUME_CONFIRMATION,
            "volume missing" if rel_volume is None else f"{rel_volume:.2f}x",
        )
    )
    # The plan targets VWAP first, then the intraday low. Once price is
    # already back at value the first target is behind us, so the second one
    # applies rather than the trade being refused.
    session_low = intraday.lowest(session)
    target = session_vwap if session_vwap < last.close else session_low
    checks.append(
        _check(
            "target below entry",
            target is not None and target < last.close,
            "nothing below price to target" if target is None else f"target {target:.2f}",
        )
    )

    return _finish(
        setup,
        ticker,
        checks,
        direction="short",
        entry=last.close,
        stop=resistance,
        target=target,
        at=last.at,
    )


# --- S2: parabolic extension -------------------------------------------------


def parabolic_short(
    ticker: str, bars: list[Bar], previous_close: float | None = None
) -> Evaluation:
    """Fading exhaustion, only after the move has visibly rolled over.

    The plan is explicit that this is counter-trend and must not be taken
    early, so the conditions require the reversal to have already begun: a
    lower high after the spike, and a rejection wick on the way up.
    """
    setup = "S2 parabolic extension"
    session = intraday.session_bars(bars)
    checks: list[Check] = []

    if len(session) < MIN_BARS["S2"]:
        checks.append(
            _check(
                "enough bars",
                False,
                f"{len(session)} bars this session, needs {MIN_BARS['S2']}",
            )
        )
        return Evaluation(setup=setup, ticker=ticker, signal=None, checks=checks)

    last = session[-1]
    session_vwap = intraday.vwap(session)
    high = intraday.swing_high(session)

    checks.append(
        _check(
            "VWAP available",
            session_vwap is not None,
            "no volume" if session_vwap is None else f"VWAP {session_vwap:.2f}",
        )
    )
    checks.append(
        _check(
            "spike high identified",
            high is not None,
            "no confirmed swing high" if high is None else f"spike {high.price:.2f}",
        )
    )
    if session_vwap is None or high is None:
        return Evaluation(setup=setup, ticker=ticker, signal=None, checks=checks)

    # "Far above VWAP" needs a scale. The session's own average distance from
    # VWAP gives one without hard-coding a percentage that means different
    # things on a $5 stock and a $500 one.
    distances = [abs(bar.close - session_vwap) for bar in session]
    typical = sum(distances) / len(distances)
    extension = high.price - session_vwap
    checks.append(
        _check(
            "extended far above VWAP",
            typical > 0 and extension > typical * 2,
            f"spike sat {extension:.2f} above VWAP; typical {typical:.2f}",
        )
    )

    spike_bar = next((bar for bar in session if bar.at == high.at), None)
    wick = intraday.upper_wick_share(spike_bar) if spike_bar else None
    checks.append(
        _check(
            "rejection wick on the spike",
            wick is not None and wick >= EXHAUSTION_WICK,
            "no wick data" if wick is None else f"upper wick {wick:.0%} of the bar",
        )
    )

    after = [bar for bar in session if bar.at > high.at]
    lower_high = bool(after) and max(
        (bar.high if bar.high is not None else bar.close) for bar in after
    ) < high.price
    checks.append(
        _check(
            "rolled over (lower high since)",
            lower_high,
            "highs since the spike are lower"
            if lower_high
            else "price has not made a lower high",
        )
    )
    checks.append(
        _check(
            "still above VWAP",
            last.close > session_vwap,
            f"close {last.close:.2f} vs VWAP {session_vwap:.2f}",
        )
    )

    # The plan lists two targets: back toward VWAP, then "prior consolidation
    # zone or intraday support". The second is the level used here, because it
    # is where the extension came from and it is fixed before the trade rather
    # than chosen to satisfy a ratio. VWAP sits between the two and is where
    # partial profit would come off.
    before_spike = [bar for bar in session if bar.at < high.at]
    target = intraday.lowest(before_spike)
    checks.append(
        _check(
            "consolidation to fall back to",
            target is not None and target < last.close,
            "no pre-spike consolidation below price"
            if target is None
            else f"came from {target:.2f}",
        )
    )

    return _finish(
        setup,
        ticker,
        checks,
        direction="short",
        entry=last.close,
        stop=high.price,
        target=target,
        at=last.at,
    )


SETUPS = {
    "L1": dip_and_rip,
    "L2": vwap_bounce,
    "S1": failed_breakout,
    "S2": parabolic_short,
}


def evaluate_all(
    ticker: str, bars: list[Bar], previous_close: float | None
) -> list[Evaluation]:
    """Run every setup against one symbol's bars."""
    return [
        setup(ticker, bars, previous_close) for setup in SETUPS.values()
    ]
