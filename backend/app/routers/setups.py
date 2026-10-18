"""Intraday setup scanner.

Live only. `stock_prices` holds one row per trading day, so there is no stored
record of what a 5-minute chart looked like at 09:47 last Tuesday and these
setups cannot be run through `scoring.validate()`. Every response says so
rather than letting a list of signals imply a track record it does not have.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.integrations.yahoo import YahooUnavailable, fetch_intraday, is_realtime
from app.models import Stock, StockPrice
from app.services import markets, sectors, setups
from app.services.tickers import tickers_in_group

router = APIRouter(prefix="/setups", tags=["setups"])

# 5-minute bars over one session: the timeframe every one of these setups is
# specified on.
WINDOW = "1d"

# One live request per symbol, so the whole universe is ~180. That is fine
# spread across a few connections and rude on one, hence the semaphore below
# rather than a low cap that makes a universe-wide board impossible.
MAX_SYMBOLS = 200

# Concurrent fetches. Enough that 180 symbols finish in seconds; few enough to
# stay a polite client of an endpoint nobody is paying for.
FETCH_CONCURRENCY = 6

# A session whose newest bar is older than this is not trading right now. Most
# of this universe is listed in Asia or Europe, where the market is shut during
# US hours — and the setups are specified for a live tape. Scoring a closed
# market produces a signal nobody can act on, so those rows are marked and
# excluded from signals unless asked for explicitly.
STALE_AFTER_MINUTES = 30

# Bar age is not only about whether a market is open. Yahoo delays non-US
# exchanges by roughly 15-20 minutes, so a Tokyo or Seoul signal describes the
# tape as it stood a quarter of an hour ago — and these setups carry stops a
# fraction of a percent wide, which that much drift goes straight through. The
# age travels with every signal so nobody reads a delayed quote as a live one,
# and this is the default ceiling for calling one actionable.
ACTIONABLE_BAR_AGE_MINUTES = 10

# The venue these setups are usable on. Not because the patterns are American,
# but because this feed is real-time only for US listings: everywhere else the
# entry price is a quarter of an hour old by the time it is read. Its session
# is therefore the tool's working window — 09:30-16:00 New York, which is
# 15:30-22:00 in central Europe and moves with daylight saving on both sides.
TRADABLE_VENUE = "MU"  # any US symbol; resolves to the US market metadata


@router.get("", summary="Which intraday setups are triggering right now")
async def scan(
    ticker: list[str] | None = Query(
        default=None, description="Repeat to scan specific symbols, e.g. ?ticker=MU&ticker=STX"
    ),
    group: str | None = Query(
        default=None, description="Industry group, e.g. data_storage"
    ),
    setup: str | None = Query(
        default=None, description=f"Limit to one setup: {', '.join(setups.SETUPS)}"
    ),
    account_equity: float | None = Query(
        default=None, gt=0, description="If given, each signal carries a position size"
    ),
    risk_fraction: float = Query(
        default=setups.DEFAULT_RISK_FRACTION,
        gt=0,
        le=0.05,
        description="Share of equity risked per trade. The plan's default is 0.005.",
    ),
    include_failed: bool = Query(
        default=False, description="Also return setups that did not trigger, with reasons"
    ),
    include_stale: bool = Query(
        default=False, description="Include symbols whose market is not trading now"
    ),
    include_delayed: bool = Query(
        default=False,
        description=(
            "Include venues this feed delays. Off by default: their entry "
            "prices are ~20 minutes old and not obtainable."
        ),
    ),
    tz: str = Query(default="UTC", description="Report the session window in this timezone"),
    max_bar_age_minutes: float = Query(
        default=ACTIONABLE_BAR_AGE_MINUTES,
        gt=0,
        description=(
            "Signals built on bars older than this are reported as not "
            "actionable. Yahoo delays non-US venues 15-20 minutes."
        ),
    ),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Evaluate the intraday setups against live 5-minute bars.

    Bars are fetched per symbol, so this is deliberately narrow: name a group
    or a few symbols rather than scanning 180 listings on every call.

    `include_failed=true` returns the full checklist for every symbol, which is
    how you tell "nothing set up today" from "the scan is not seeing data".
    """
    symbols = await _resolve(db, ticker, group)
    if not symbols:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Name at least one ticker or a group, e.g. ?group=data_storage.",
        )
    if len(symbols) > MAX_SYMBOLS:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=(
                f"{len(symbols)} symbols would mean {len(symbols)} live requests. "
                f"Narrow to {MAX_SYMBOLS} or fewer."
            ),
        )
    if setup and setup not in setups.SETUPS:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Unknown setup {setup!r}. Try one of {sorted(setups.SETUPS)}.",
        )

    return await run_scan(
        db,
        symbols,
        setup=setup,
        account_equity=account_equity,
        risk_fraction=risk_fraction,
        include_failed=include_failed,
        include_stale=include_stale,
        include_delayed=include_delayed,
        tz=tz,
        max_bar_age_minutes=max_bar_age_minutes,
    )


async def run_scan(
    db: AsyncSession,
    symbols: list[str],
    *,
    setup: str | None = None,
    account_equity: float | None = None,
    risk_fraction: float = setups.DEFAULT_RISK_FRACTION,
    include_failed: bool = False,
    include_stale: bool = False,
    include_delayed: bool = False,
    tz: str = "UTC",
    max_bar_age_minutes: float = ACTIONABLE_BAR_AGE_MINUTES,
) -> dict:
    """Scan resolved symbols and report what fired.

    Extracted from the endpoint so a second caller gets the scanner's actual
    rules rather than a second implementation of them. Staleness, feed delay
    and bar age all decide whether a signal is real, and a caller that
    reimplemented any of the three would quietly disagree with /setups about
    which trades exist.
    """
    delayed = [symbol for symbol in symbols if not is_realtime(symbol)]
    if not include_delayed:
        symbols = [symbol for symbol in symbols if is_realtime(symbol)]

    signals: list[dict] = []
    considered: list[dict] = []

    # Bars first, because the prior close depends on which session the bars
    # belong to — see _previous_closes.
    sessions, unavailable = await _fetch_sessions(symbols)
    previous_closes = await _previous_closes(db, sessions)

    stale: list[str] = []
    for symbol, bars in sessions.items():
        # A group like data_storage spans Seoul, Taipei and Tokyo as well as
        # New York. Those markets are shut during the US session, so their
        # newest bar is yesterday's close and any signal from it is a trade
        # nobody can take.
        if _minutes_old(bars) > STALE_AFTER_MINUTES:
            stale.append(symbol)
            if not include_stale:
                continue

        evaluations = (
            [setups.SETUPS[setup](symbol, bars, previous_closes.get(symbol))]
            if setup
            else setups.evaluate_all(symbol, bars, previous_closes.get(symbol))
        )
        age = round(_minutes_old(bars), 1)
        for evaluation in evaluations:
            if evaluation.signal:
                payload = evaluation.signal.as_dict()
                # The entry is the last bar's close. If that bar is fifteen
                # minutes old the price has moved on, and a stop a quarter of a
                # percent wide has no chance of surviving the difference.
                payload["bars_minutes_old"] = age
                payload["actionable"] = age <= max_bar_age_minutes
                if not payload["actionable"]:
                    payload["note"] = (
                        f"Built on a bar {age:.0f} minutes old — the entry price "
                        "is not current. Non-US venues are delayed on this feed."
                    )
                if account_equity:
                    payload["position"] = setups.position_size(
                        account_equity,
                        evaluation.signal.entry,
                        evaluation.signal.stop,
                        risk_fraction,
                    )
                signals.append(payload)
            elif include_failed:
                considered.append(evaluation.as_dict())

    return {
        "scanned": len(sessions) - (0 if include_stale else len(stale)),
        "unavailable": unavailable,
        "stale_markets": stale,
        "window": WINDOW,
        "session": _session(tz),
        "delayed_venues": [] if include_delayed else delayed,
        "signals": signals,
        "actionable_signals": sum(1 for item in signals if item["actionable"]),
        "considered": considered if include_failed else [],
        "caveat": (
            "Live conditions, not a forecast and not a track record. Intraday "
            "bars are fetched and never stored, so these setups cannot be run "
            "through GET /scores/validation the way the daily score is — their "
            "hit rate on this universe is unmeasured. Symbols in stale_markets "
            "are not trading right now and were skipped. A signal with "
            "actionable=false was built on a delayed bar and its entry price is "
            "not current. Levels come from the chart; sizing assumes the stop "
            "is honoured."
        ),
    }


async def _resolve(
    db: AsyncSession, tickers: list[str] | None, group: str | None
) -> list[str]:
    if group:
        if not sectors.sectors_in(group.strip().lower()):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"Unknown group {group!r}. Try GET /stocks/sectors.",
            )
        members = await tickers_in_group(db, group)
        if tickers:
            wanted = {symbol.upper() for symbol in tickers}
            return [symbol for symbol in members if symbol.upper() in wanted]
        return members
    return [symbol.upper() for symbol in (tickers or [])]


async def _previous_closes(db: AsyncSession, sessions: dict[str, list]) -> dict[str, float]:
    """The last stored daily close *before* the session each symbol is in.

    "Up on the day" needs yesterday's close, and the intraday feed does not
    carry it — the daily table does, which is one of the few places the two
    timeframes meet.

    Strictly before the session date, which is the whole subtlety. Taking the
    most recent stored close instead returns *today's* once a backfill has run
    or the daily job has fired, so "up on the day" compares a price against
    itself and L1 can never trigger. That failure is silent: the scan returns
    no signals and looks like a quiet market.
    """
    if not sessions:
        return {}

    rows = (
        await db.execute(
            select(Stock.ticker, StockPrice.close, StockPrice.price_date)
            .join(StockPrice, StockPrice.ticker_id == Stock.id)
            .where(Stock.ticker.in_(list(sessions)))
            .order_by(Stock.ticker, StockPrice.price_date.desc())
        )
    ).all()

    session_dates = {
        symbol: bars[-1].at.date() for symbol, bars in sessions.items() if bars
    }

    latest: dict[str, float] = {}
    for symbol, close, price_date in rows:
        if symbol in latest or close is None:
            continue
        session_date = session_dates.get(symbol)
        if session_date and price_date.date() >= session_date:
            continue
        latest[symbol] = close
    return latest


@router.get("/size", summary="Shares to trade for a given entry and stop")
async def size(
    account_equity: float = Query(gt=0),
    entry: float = Query(gt=0),
    stop: float = Query(gt=0),
    risk_fraction: float = Query(default=setups.DEFAULT_RISK_FRACTION, gt=0, le=0.05),
) -> dict:
    """The stop distance decides the size, so every trade risks the same amount.

    Separate from the scan because it is useful on a trade you found yourself.
    """
    return setups.position_size(account_equity, entry, stop, risk_fraction)


async def _fetch_sessions(symbols: list[str]) -> tuple[dict[str, list], list[str]]:
    """Intraday bars for many symbols, a few connections at a time.

    Serially this took one round trip per symbol, which put a universe-wide
    scan out of reach and forced a cap low enough that "all the stocks" was
    not a question the endpoint could answer.
    """
    semaphore = asyncio.Semaphore(FETCH_CONCURRENCY)
    refused = False

    async def _one(symbol: str) -> tuple[str, list | None]:
        nonlocal refused
        if refused:
            return symbol, None
        async with semaphore:
            try:
                return symbol, await fetch_intraday(symbol, WINDOW)
            except YahooUnavailable:
                # A refusal applies to the client, not the symbol: the rest of
                # the batch would be refused too, so stop asking.
                refused = True
                return symbol, None

    results = await asyncio.gather(*(_one(symbol) for symbol in symbols))

    sessions: dict[str, list] = {}
    unavailable: list[str] = []
    for symbol, bars in results:
        if bars:
            sessions[symbol] = bars
        else:
            unavailable.append(symbol)
    return sessions, unavailable


def _minutes_old(bars: list) -> float:
    return (datetime.now(timezone.utc) - bars[-1].at).total_seconds() / 60


@router.get("/board", summary="Every symbol's setup conditions, as a matrix")
async def board(
    ticker: list[str] | None = Query(default=None),
    group: str | None = Query(default=None, description="Omit for the whole universe"),
    setup: str | None = Query(default=None, description=f"One of {', '.join(setups.SETUPS)}"),
    min_passed: int = Query(
        default=0, ge=0, description="Only rows with at least this many conditions met"
    ),
    include_stale: bool = Query(
        default=False, description="Include symbols whose market is not trading now"
    ),
    include_delayed: bool = Query(
        default=False, description="Include venues this feed delays by ~20 minutes"
    ),
    tz: str = Query(default="UTC", description="Report the session window in this timezone"),
    limit: int = Query(default=40, ge=1, le=500),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """The same checks as GET /setups, as one row per symbol per setup.

    `include_failed` on the scan returns every condition of every setup in
    full, which is readable for four symbols and unreadable for a hundred.
    This reports each row as a count and a list of what is missing, sorted by
    how close it came — so "which of these is one condition away" is a
    question you can answer by looking.
    """
    symbols = await _resolve(db, ticker, group) or await _all_active(db)
    if len(symbols) > MAX_SYMBOLS:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"{len(symbols)} symbols exceeds the {MAX_SYMBOLS} cap.",
        )
    if setup and setup not in setups.SETUPS:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Unknown setup {setup!r}. Try one of {sorted(setups.SETUPS)}.",
        )

    delayed = [symbol for symbol in symbols if not is_realtime(symbol)]
    if not include_delayed:
        symbols = [symbol for symbol in symbols if is_realtime(symbol)]

    sessions, unavailable = await _fetch_sessions(symbols)
    previous_closes = await _previous_closes(db, sessions)

    rows: list[dict] = []
    stale = 0
    for symbol, bars in sessions.items():
        age = _minutes_old(bars)
        live = age <= STALE_AFTER_MINUTES
        if not live:
            stale += 1
            if not include_stale:
                continue

        evaluations = (
            [setups.SETUPS[setup](symbol, bars, previous_closes.get(symbol))]
            if setup
            else setups.evaluate_all(symbol, bars, previous_closes.get(symbol))
        )
        for evaluation in evaluations:
            passed = [check for check in evaluation.checks if check.passed]
            rows.append(
                {
                    "ticker": symbol,
                    "setup": evaluation.setup,
                    "live": live,
                    "bars_minutes_old": round(age, 1),
                    "actionable": age <= ACTIONABLE_BAR_AGE_MINUTES,
                    "passed": len(passed),
                    "total": len(evaluation.checks),
                    "triggered": evaluation.signal is not None,
                    "marks": "".join(
                        "+" if check.passed else "-" for check in evaluation.checks
                    ),
                    "failed": evaluation.failed,
                    "readings": {
                        check.name: check.detail for check in evaluation.checks
                    },
                }
            )

    # Closest first: a symbol one condition away is the one worth watching.
    rows.sort(key=lambda row: (row["triggered"], row["passed"] / max(row["total"], 1)), reverse=True)
    shown = [row for row in rows if row["passed"] >= min_passed][:limit]

    return {
        "scanned": len(sessions),
        "unavailable": unavailable,
        "stale_markets": stale,
        "window": WINDOW,
        "session": _session(tz),
        "delayed_venues": [] if include_delayed else delayed,
        "triggered": sum(1 for row in rows if row["triggered"]),
        "rows": shown,
        "caveat": (
            "Conditions as they stand, not a forecast. Rows marked live=false "
            "come from a market that is closed right now — most of this "
            "universe is listed outside the US — and are excluded unless "
            "include_stale=true. Rows with actionable=false are built on a bar "
            "old enough that the entry price has moved on: this feed delays "
            "non-US venues 15-20 minutes. Nothing here has a measured hit rate."
        ),
    }


async def _all_active(db: AsyncSession) -> list[str]:
    return list(
        (
            await db.execute(
                select(Stock.ticker).where(Stock.is_active.is_(True)).order_by(Stock.ticker)
            )
        ).scalars()
    )


def _session(tz: str) -> dict:
    """Whether the tradable window is open, and when that next changes.

    Reported on every response because a scan run outside it is not wrong, it
    is just early — and "no signals" reads very differently once you know the
    market has been shut for nine hours.
    """
    try:
        return markets.session_state(markets.resolve(TRADABLE_VENUE), tz=tz)
    except Exception:
        # An unknown timezone is the caller's typo, not a reason to fail the
        # scan they actually asked for.
        return markets.session_state(markets.resolve(TRADABLE_VENUE), tz="UTC")
