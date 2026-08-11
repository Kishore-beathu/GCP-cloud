"""Intraday setup scanner.

Live only. `stock_prices` holds one row per trading day, so there is no stored
record of what a 5-minute chart looked like at 09:47 last Tuesday and these
setups cannot be run through `scoring.validate()`. Every response says so
rather than letting a list of signals imply a track record it does not have.
"""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.integrations.yahoo import YahooUnavailable, fetch_intraday
from app.models import Stock, StockPrice
from app.services import sectors, setups
from app.services.tickers import tickers_in_group

router = APIRouter(prefix="/setups", tags=["setups"])

# 5-minute bars over one session: the timeframe every one of these setups is
# specified on.
WINDOW = "1d"

# Scanning the whole universe would be ~180 live requests per call. The setups
# are specified for liquid US names, so the caller names a group or a handful
# of symbols.
MAX_SYMBOLS = 25


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

    previous_closes = await _previous_closes(db, symbols)

    signals: list[dict] = []
    considered: list[dict] = []
    unavailable: list[str] = []

    for symbol in symbols:
        try:
            bars = await fetch_intraday(symbol, WINDOW)
        except YahooUnavailable:
            # One refusal means the rest of the batch will be refused too.
            unavailable.extend(symbols[symbols.index(symbol) :])
            break
        if not bars:
            unavailable.append(symbol)
            continue

        evaluations = (
            [setups.SETUPS[setup](symbol, bars, previous_closes.get(symbol))]
            if setup
            else setups.evaluate_all(symbol, bars, previous_closes.get(symbol))
        )
        for evaluation in evaluations:
            if evaluation.signal:
                payload = evaluation.signal.as_dict()
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
        "scanned": len(symbols) - len(unavailable),
        "unavailable": unavailable,
        "window": WINDOW,
        "signals": signals,
        "considered": considered if include_failed else [],
        "caveat": (
            "Live conditions, not a forecast and not a track record. Intraday "
            "bars are fetched and never stored, so these setups cannot be run "
            "through GET /scores/validation the way the daily score is — their "
            "hit rate on this universe is unmeasured. Levels come from the "
            "chart; sizing assumes the stop is honoured."
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


async def _previous_closes(db: AsyncSession, symbols: list[str]) -> dict[str, float]:
    """Each symbol's most recent stored daily close.

    "Up on the day" needs yesterday's close, and the intraday feed does not
    carry it. The daily table does, which is one of the few places these two
    timeframes meet.
    """
    rows = (
        await db.execute(
            select(Stock.ticker, StockPrice.close, StockPrice.price_date)
            .join(StockPrice, StockPrice.ticker_id == Stock.id)
            .where(Stock.ticker.in_(symbols))
            .order_by(Stock.ticker, StockPrice.price_date.desc())
        )
    ).all()

    latest: dict[str, float] = {}
    for symbol, close, _ in rows:
        if symbol not in latest and close is not None:
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
