"""In-process pub/sub for the real-time ticker WebSocket.

A single-process hub is the right size for Week 1. When the API scales past one
worker, swap the internals for Redis pub/sub — the public methods here are the
seam that keeps that change local.
"""

from __future__ import annotations

import asyncio
import logging
from collections import defaultdict
from typing import Any

from fastapi import WebSocket

logger = logging.getLogger(__name__)


class TickerHub:
    """Tracks WebSocket connections and their ticker subscriptions."""

    def __init__(self) -> None:
        self._subscriptions: dict[str, set[WebSocket]] = defaultdict(set)
        self._sockets: dict[WebSocket, set[str]] = {}
        self._lock = asyncio.Lock()

    async def connect(self, websocket: WebSocket, ticker: str) -> None:
        """Accept a socket and give it an initial subscription."""
        await websocket.accept()
        async with self._lock:
            self._sockets[websocket] = set()
        await self.subscribe(websocket, [ticker])

    async def disconnect(self, websocket: WebSocket) -> None:
        """Drop a socket and all of its subscriptions."""
        async with self._lock:
            for ticker in self._sockets.pop(websocket, set()):
                self._subscriptions[ticker].discard(websocket)
                if not self._subscriptions[ticker]:
                    del self._subscriptions[ticker]

    async def subscribe(self, websocket: WebSocket, tickers: list[str]) -> set[str]:
        """Add tickers to a socket's subscription set and return the new set."""
        async with self._lock:
            current = self._sockets.setdefault(websocket, set())
            for ticker in tickers:
                symbol = ticker.strip().upper()
                if symbol:
                    current.add(symbol)
                    self._subscriptions[symbol].add(websocket)
            return set(current)

    async def unsubscribe(self, websocket: WebSocket, tickers: list[str]) -> set[str]:
        """Remove tickers from a socket's subscription set and return the new set."""
        async with self._lock:
            current = self._sockets.setdefault(websocket, set())
            for ticker in tickers:
                symbol = ticker.strip().upper()
                current.discard(symbol)
                self._subscriptions[symbol].discard(websocket)
                if not self._subscriptions[symbol]:
                    self._subscriptions.pop(symbol, None)
            return set(current)

    def subscribed_tickers(self) -> set[str]:
        """Every ticker with at least one listener — the price job's work list."""
        return set(self._subscriptions)

    async def broadcast_price(self, ticker: str, message: dict[str, Any]) -> None:
        """Send a price update to the subscribers of one ticker."""
        async with self._lock:
            targets = list(self._subscriptions.get(ticker.upper(), ()))
        await self._send_many(targets, message)

    async def broadcast_alert(self, payload: dict[str, Any]) -> None:
        """Send an alert firing to every connected socket."""
        async with self._lock:
            targets = list(self._sockets)
        await self._send_many(targets, {"type": "alert", **payload})

    async def _send_many(self, targets: list[WebSocket], message: dict[str, Any]) -> None:
        for websocket in targets:
            try:
                await websocket.send_json(message)
            except Exception:
                # A send failure means the peer is gone; reap it rather than
                # letting a dead socket keep receiving broadcasts.
                logger.debug("Dropping unreachable WebSocket", exc_info=True)
                await self.disconnect(websocket)


ticker_hub = TickerHub()
