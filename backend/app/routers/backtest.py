"""Backtesting endpoint."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.schemas import BacktestResponse
from app.services.backtest import run_backtest

router = APIRouter(prefix="/backtest", tags=["backtest"])


@router.get("", response_model=BacktestResponse, summary="Historical news impact analysis")
async def backtest(
    ticker: str = Query(description="Symbol to analyse, e.g. MRNA"),
    days: int = Query(default=90, ge=1, le=1825),
    db: AsyncSession = Depends(get_db),
) -> BacktestResponse:
    try:
        return await run_backtest(db, ticker, days)
    except LookupError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
