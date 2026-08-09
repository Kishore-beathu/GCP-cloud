"""Real-time ticker WebSocket.

Protocol
--------
Server pushes::

    {"type": "snapshot", "ticker": "MRNA", "price": 1.2, "recent_news": [...]}
    {"type": "price_update", "ticker": "MRNA", "price": 1.2, "change": 0.4, ...}
    {"type": "alert", "headline": "...", "sentiment": "positive", ...}

Client sends::

    {"action": "subscribe",   "tickers": ["MRNA", "BNTX"]}
    {"action": "unsubscribe", "tickers": ["BNTX"]}
    {"action": "ping"}
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_session_factory
from app.models import NewsArticle, SentimentScore, Stock, StockPrice
from app.security import authorize_websocket
from app.services.streams import ticker_hub

logger = logging.getLogger(__name__)
router = APIRouter(tags=["realtime"])


async def _build_snapshot(db: AsyncSession, ticker: str) -> dict:
    """Latest price plus the five most recent headlines for one ticker."""
    symbol = ticker.strip().upper()
    stock = (
        await db.execute(select(Stock).where(Stock.ticker == symbol))
    ).scalar_one_or_none()
    if stock is None:
        return {"type": "error", "ticker": symbol, "detail": "Ticker is not tracked"}

    prices = list(
        (
            await db.execute(
                select(StockPrice)
                .where(StockPrice.ticker_id == stock.id)
                .order_by(StockPrice.price_date.desc())
                .limit(2)
            )
        ).scalars()
    )
    latest = prices[0] if prices else None
    previous = prices[1] if len(prices) > 1 else None
    change = None
    if latest and previous and previous.close:
        change = round((latest.close - previous.close) / previous.close * 100, 4)

    rows = (
        await db.execute(
            select(NewsArticle, SentimentScore)
            .outerjoin(SentimentScore, SentimentScore.article_id == NewsArticle.id)
            .where(NewsArticle.ticker_id == stock.id)
            .order_by(NewsArticle.published_at.desc())
            .limit(5)
        )
    ).all()

    return {
        "type": "snapshot",
        "ticker": symbol,
        "company_name": stock.company_name,
        "price": latest.close if latest else None,
        "change": change,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "recent_news": [
            {
                "id": article.id,
                "headline": article.headline,
                "url": article.url,
                "published_at": article.published_at.isoformat(),
                "sentiment": score.sentiment if score else None,
                "score": score.score if score else None,
            }
            for article, score in rows
        ],
    }


@router.websocket("/ws/tickers/{ticker}")
async def ticker_socket(websocket: WebSocket, ticker: str, token: str | None = None) -> None:
    """Stream prices and alerts for one or more tickers.

    The token arrives as a query parameter because browsers cannot set headers
    on a WebSocket handshake. An unauthorised connection is closed with 1008
    (policy violation) before the hub ever sees it.
    """
    if authorize_websocket(token) is None:
        await websocket.close(code=1008, reason="Sign in to stream prices")
        logger.info("Rejected unauthenticated WebSocket connection")
        return

    await ticker_hub.connect(websocket, ticker)
    session_factory = get_session_factory()

    try:
        async with session_factory() as db:
            await websocket.send_json(await _build_snapshot(db, ticker))

        while True:
            message = await websocket.receive_json()
            action = str(message.get("action", "")).lower()
            tickers = [str(t) for t in message.get("tickers", []) if str(t).strip()]

            if action == "subscribe" and tickers:
                current = await ticker_hub.subscribe(websocket, tickers)
                async with session_factory() as db:
                    for symbol in tickers:
                        await websocket.send_json(await _build_snapshot(db, symbol))
                await websocket.send_json(
                    {"type": "subscribed", "tickers": sorted(current)}
                )
            elif action == "unsubscribe" and tickers:
                current = await ticker_hub.unsubscribe(websocket, tickers)
                await websocket.send_json(
                    {"type": "subscribed", "tickers": sorted(current)}
                )
            elif action == "ping":
                await websocket.send_json(
                    {"type": "pong", "timestamp": datetime.now(timezone.utc).isoformat()}
                )
            else:
                await websocket.send_json(
                    {"type": "error", "detail": f"Unsupported action {action!r}"}
                )
    except WebSocketDisconnect:
        logger.debug("WebSocket disconnected")
    except Exception:
        logger.exception("WebSocket handler failed; closing connection")
    finally:
        await ticker_hub.disconnect(websocket)
