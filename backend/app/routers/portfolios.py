"""Paper-trading portfolio endpoints."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models import Portfolio, Stock, Trade, TradeSide
from app.schemas import (
    PortfolioCreate,
    PortfolioDetail,
    PortfolioOut,
    SimulationRequest,
    SimulationResponse,
    TradeCreate,
    TradeOut,
)
from app.services.portfolio import (
    InsufficientFunds,
    InsufficientShares,
    execute_trade,
    latest_prices,
    simulate_sentiment_strategy,
    value_portfolio,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/portfolios", tags=["portfolios"])


async def _get_portfolio_or_404(db: AsyncSession, portfolio_id: int) -> Portfolio:
    portfolio = await db.get(Portfolio, portfolio_id)
    if portfolio is None:
        raise HTTPException(status_code=404, detail=f"Portfolio {portfolio_id} not found")
    return portfolio


async def _detail(db: AsyncSession, portfolio: Portfolio) -> PortfolioDetail:
    valuation, positions = await value_portfolio(db, portfolio)
    return PortfolioDetail(
        id=portfolio.id,
        user_id=portfolio.user_id,
        name=portfolio.name,
        starting_cash=portfolio.starting_cash,
        cash=portfolio.cash,
        created_at=portfolio.created_at,
        positions=positions,
        cash_value=round(valuation.cash, 2),
        positions_value=round(valuation.positions_value, 2),
        total_value=round(valuation.total_value, 2),
        realised_pnl=round(valuation.realised_pnl, 2),
        unrealised_pnl=round(valuation.unrealised_pnl, 2),
        total_return_pct=(
            round(valuation.total_return_pct, 2)
            if valuation.total_return_pct is not None
            else None
        ),
    )


@router.get("", response_model=list[PortfolioOut], summary="List portfolios")
async def list_portfolios(
    user_id: str = Query(default="local"),
    db: AsyncSession = Depends(get_db),
) -> list[Portfolio]:
    rows = (
        await db.execute(
            select(Portfolio).where(Portfolio.user_id == user_id).order_by(Portfolio.id)
        )
    ).scalars()
    return list(rows)


@router.post("", response_model=PortfolioOut, status_code=201, summary="Create a portfolio")
async def create_portfolio(
    payload: PortfolioCreate, db: AsyncSession = Depends(get_db)
) -> Portfolio:
    portfolio = Portfolio(
        user_id=payload.user_id,
        name=payload.name,
        starting_cash=payload.starting_cash,
        cash=payload.starting_cash,
    )
    db.add(portfolio)
    await db.commit()
    await db.refresh(portfolio)
    return portfolio


@router.get("/{portfolio_id}", response_model=PortfolioDetail, summary="Portfolio with valuation")
async def get_portfolio(
    portfolio_id: int, db: AsyncSession = Depends(get_db)
) -> PortfolioDetail:
    portfolio = await _get_portfolio_or_404(db, portfolio_id)
    return await _detail(db, portfolio)


@router.delete("/{portfolio_id}", status_code=204, summary="Delete a portfolio")
async def delete_portfolio(portfolio_id: int, db: AsyncSession = Depends(get_db)) -> None:
    portfolio = await _get_portfolio_or_404(db, portfolio_id)
    await db.delete(portfolio)
    await db.commit()


@router.get(
    "/{portfolio_id}/trades", response_model=list[TradeOut], summary="Trade log, newest first"
)
async def list_trades(
    portfolio_id: int,
    limit: int = Query(default=100, ge=1, le=1000),
    db: AsyncSession = Depends(get_db),
) -> list[TradeOut]:
    await _get_portfolio_or_404(db, portfolio_id)
    rows = (
        await db.execute(
            select(Trade, Stock.ticker)
            .join(Stock, Stock.id == Trade.ticker_id)
            .where(Trade.portfolio_id == portfolio_id)
            .order_by(Trade.executed_at.desc(), Trade.id.desc())
            .limit(limit)
        )
    ).all()
    return [
        TradeOut(
            id=trade.id,
            ticker=ticker,
            side=TradeSide(trade.side),
            quantity=trade.quantity,
            price=trade.price,
            executed_at=trade.executed_at,
            rationale=trade.rationale,
        )
        for trade, ticker in rows
    ]


@router.post(
    "/{portfolio_id}/trades",
    response_model=TradeOut,
    status_code=201,
    summary="Record a simulated trade",
)
async def create_trade(
    portfolio_id: int, payload: TradeCreate, db: AsyncSession = Depends(get_db)
) -> TradeOut:
    portfolio = await _get_portfolio_or_404(db, portfolio_id)
    stock = (
        await db.execute(select(Stock).where(Stock.ticker == payload.ticker))
    ).scalar_one_or_none()
    if stock is None:
        raise HTTPException(status_code=404, detail=f"Unknown ticker {payload.ticker}")

    price = payload.price
    if price is None:
        prices = await latest_prices(db, [stock.ticker])
        price = prices.get(stock.ticker)
        if price is None:
            raise HTTPException(
                status_code=422,
                detail=(
                    f"No stored price for {stock.ticker}; pass an explicit price "
                    "or backfill history first"
                ),
            )

    try:
        trade = await execute_trade(
            db, portfolio, stock, payload.side, payload.quantity, price, payload.rationale
        )
    except (InsufficientFunds, InsufficientShares) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    await db.commit()
    await db.refresh(trade)
    return TradeOut(
        id=trade.id,
        ticker=stock.ticker,
        side=TradeSide(trade.side),
        quantity=trade.quantity,
        price=trade.price,
        executed_at=trade.executed_at,
        rationale=trade.rationale,
    )


@router.post(
    "/{portfolio_id}/simulate",
    response_model=SimulationResponse,
    summary="Replay the sentiment strategy over stored history",
)
async def simulate(
    portfolio_id: int,
    payload: SimulationRequest,
    db: AsyncSession = Depends(get_db),
) -> SimulationResponse:
    """Run the built-in sentiment strategy, recording its trades in this portfolio.

    Trades accumulate, so run this against a fresh portfolio for a clean result.
    """
    portfolio = await _get_portfolio_or_404(db, portfolio_id)
    result = await simulate_sentiment_strategy(
        db,
        portfolio,
        days=payload.days,
        min_score=payload.min_score,
        min_confidence=payload.min_confidence,
        position_size_pct=payload.position_size_pct,
        hold_days=payload.hold_days,
    )
    return SimulationResponse(
        portfolio_id=portfolio.id,
        **result.as_dict(),
        valuation=await _detail(db, portfolio),
    )
