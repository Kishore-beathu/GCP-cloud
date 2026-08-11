"""Live trade streaming from Finnhub's WebSocket API.

This is the only genuinely real-time price source in the platform: Alpha
Vantage is REST-only, so its quotes are polled. One connection to
``wss://ws.finnhub.io`` carries every symbol we care about, and each trade tick
is fanned out to browser clients through the same ``ticker_hub`` the polling
job uses.

Three behaviours make it safe to leave running:

* **Coalescing.** A liquid symbol can print many trades a second. Ticks update
  an in-memory latest-price map, and a flush loop broadcasts at most one
  message per symbol per ``finnhub_stream_flush_seconds`` — the browser gets
  current prices without a firehose.
* **Demand-driven subscriptions.** Only tickers with at least one connected
  viewer are subscribed, re-checked periodically, and capped at
  ``finnhub_stream_max_symbols`` because plans limit concurrent symbols.
* **Self-healing.** Disconnects reconnect with exponential backoff. Failure to
  stream never breaks the REST API; it just falls back to polled prices.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from datetime import datetime, timezone

import websockets
from websockets.exceptions import InvalidStatus

# A refused handshake with one of these is about the credential, not about
# timing. Reconnecting cannot fix it and every attempt spends quota, so the
# stream stops and says why — the same rule the REST client already follows.
_FATAL_HANDSHAKE_STATUSES = frozenset({401, 403})


def _retry_after(exc: InvalidStatus) -> float | None:
    """The vendor's own Retry-After, in seconds, when it sent a usable one."""
    raw = exc.response.headers.get("Retry-After")
    try:
        return float(raw) if raw else None
    except (TypeError, ValueError):
        # The header also permits an HTTP date. Falling back to our own
        # default beats parsing a date to wait an unknown length of time.
        return None
from sqlalchemy import select
from websockets.exceptions import ConnectionClosed

from app.config import get_settings
from app.database import get_session_factory
from app.models import Stock, StockPrice
from app.services.streams import ticker_hub

logger = logging.getLogger(__name__)

WS_URL = "wss://ws.finnhub.io"


class FinnhubStream:
    """Owns one Finnhub WebSocket connection and its subscription set."""

    def __init__(self) -> None:
        self._task: asyncio.Task | None = None
        self._flush_task: asyncio.Task | None = None
        self._running = False
        self._subscribed: set[str] = set()
        # Symbol -> most recent trade price not yet broadcast.
        self._pending: dict[str, float] = {}
        # Symbol -> latest streamed price, whether or not it has been flushed.
        self._latest: dict[str, float] = {}
        # Symbol -> previous close, for the percentage change shown in the UI.
        self._reference: dict[str, float] = {}
        self._connected = False

    # --- lifecycle ---------------------------------------------------------

    async def start(self) -> None:
        """Begin streaming. A missing API key is a no-op, not an error."""
        settings = get_settings()
        if not settings.finnhub_api_key:
            logger.info("Finnhub stream disabled: FINNHUB_API_KEY is not set")
            return
        if not settings.finnhub_stream_enabled:
            logger.info("Finnhub stream disabled by configuration")
            return
        if self._running:
            return

        self._running = True
        self._task = asyncio.create_task(self._run(), name="finnhub-stream")
        self._flush_task = asyncio.create_task(self._flush_loop(), name="finnhub-flush")
        logger.info("Finnhub live price stream started")

    async def stop(self) -> None:
        """Stop streaming and wait for the tasks to unwind."""
        self._running = False
        for task in (self._task, self._flush_task):
            if task is not None:
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task
        self._task = self._flush_task = None
        self._subscribed.clear()
        self._pending.clear()
        self._latest.clear()
        self._connected = False

    @property
    def connected(self) -> bool:
        return self._connected

    def live_symbols(self) -> set[str]:
        """Symbols with a streamed price.

        The polling job skips these: replaying a stored close over a live trade
        price would make the UI flicker between two different numbers.
        """
        return set(self._latest)

    def status(self) -> dict:
        return {
            "enabled": self._running,
            "connected": self._connected,
            "subscribed": sorted(self._subscribed),
            "live_prices": len(self._latest),
        }

    # --- connection --------------------------------------------------------

    async def _run(self) -> None:
        """Connect, stream, and reconnect forever until stopped."""
        settings = get_settings()
        backoff = settings.finnhub_stream_backoff_seconds

        while self._running:
            try:
                url = f"{WS_URL}?token={settings.finnhub_api_key}"
                async with websockets.connect(url, ping_interval=20) as socket:
                    self._connected = True
                    backoff = settings.finnhub_stream_backoff_seconds  # reset on success
                    logger.info("Finnhub stream connected")
                    self._subscribed.clear()
                    # First loop to finish ends the session: the read loop
                    # returns when the socket closes, and the subscription loop
                    # would otherwise keep sleeping against a dead connection
                    # forever, blocking the reconnect.
                    await self._until_first_finishes(
                        self._read_loop(socket),
                        self._subscription_loop(socket),
                    )
            except asyncio.CancelledError:
                raise
            except InvalidStatus as exc:
                # The handshake was answered and refused. Why it was refused
                # decides what to do next, and treating every refusal the same
                # meant a rate limit was retried from a two-second backoff --
                # which is what produced the rate limit -- and a bad key was
                # retried forever.
                status = exc.response.status_code
                if status in _FATAL_HANDSHAKE_STATUSES:
                    logger.error(
                        "Finnhub stream rejected the token (HTTP %s); "
                        "not retrying. Check FINNHUB_API_KEY.",
                        status,
                    )
                    self._running = False
                    self._connected = False
                    break
                if status == 429:
                    # Honour the vendor's own number when it sends one, rather
                    # than guessing at the interval it just told us was wrong.
                    backoff = max(
                        backoff,
                        _retry_after(exc) or settings.finnhub_stream_rate_limit_backoff_seconds,
                    )
                    logger.warning(
                        "Finnhub stream rate limited (HTTP 429); next attempt in %.0fs",
                        backoff,
                    )
                else:
                    logger.warning("Finnhub stream refused with HTTP %s", status)
            except (ConnectionClosed, OSError) as exc:
                logger.warning("Finnhub stream disconnected: %s", exc)
            except Exception:  # noqa: BLE001 - streaming must never kill the app
                logger.exception("Finnhub stream error")
            finally:
                self._connected = False

            if not self._running:
                break
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, settings.finnhub_stream_max_backoff_seconds)

    @staticmethod
    async def _until_first_finishes(*coroutines) -> None:
        """Run coroutines until one finishes, then cancel the rest and re-raise."""
        tasks = [asyncio.ensure_future(coroutine) for coroutine in coroutines]
        try:
            done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        except asyncio.CancelledError:
            for task in tasks:
                task.cancel()
            raise

        for task in pending:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
        for task in done:
            task.result()  # surface the failure that ended the session

    async def _read_loop(self, socket) -> None:
        """Consume trade messages and record the latest price per symbol."""
        async for raw in socket:
            try:
                message = json.loads(raw)
            except (TypeError, ValueError):
                continue

            kind = message.get("type")
            if kind == "trade":
                for trade in message.get("data") or []:
                    symbol = str(trade.get("s", "")).upper()
                    price = trade.get("p")
                    if symbol and isinstance(price, (int, float)):
                        self._pending[symbol] = float(price)
                        self._latest[symbol] = float(price)
            elif kind == "error":
                # Bad symbol or plan restriction: log once, keep the socket.
                logger.warning("Finnhub stream error message: %s", message.get("msg"))

    async def _subscription_loop(self, socket) -> None:
        """Keep the upstream subscription set aligned with viewer demand."""
        settings = get_settings()
        while self._running:
            wanted = await self._wanted_symbols()

            for symbol in wanted - self._subscribed:
                await socket.send(json.dumps({"type": "subscribe", "symbol": symbol}))
                self._subscribed.add(symbol)
            for symbol in self._subscribed - wanted:
                await socket.send(json.dumps({"type": "unsubscribe", "symbol": symbol}))
                self._subscribed.discard(symbol)
                # Drop the stale price so the polling job resumes covering it.
                self._latest.pop(symbol, None)
                self._pending.pop(symbol, None)

            await asyncio.sleep(settings.finnhub_stream_resync_seconds)

    async def _wanted_symbols(self) -> set[str]:
        """Which symbols to stream: what viewers are watching, within the cap."""
        settings = get_settings()
        watched = sorted(ticker_hub.subscribed_tickers())
        if not watched:
            return set()

        capped = set(watched[: max(1, settings.finnhub_stream_max_symbols)])
        missing = capped - self._reference.keys()
        if missing:
            await self._load_reference_closes(missing)
        return capped

    async def _load_reference_closes(self, symbols: set[str]) -> None:
        """Cache each symbol's previous close so ticks can show a change %."""
        try:
            async with get_session_factory()() as db:
                rows = (
                    await db.execute(
                        select(Stock.ticker, StockPrice.close, StockPrice.price_date)
                        .join(Stock, Stock.id == StockPrice.ticker_id)
                        .where(Stock.ticker.in_(symbols))
                        .order_by(Stock.ticker, StockPrice.price_date.desc())
                    )
                ).all()
            for ticker, close, _ in rows:
                self._reference.setdefault(ticker, close)  # newest row wins
        except Exception:  # noqa: BLE001 - a missing baseline only costs the change %
            logger.exception("Could not load reference closes for the price stream")

    # --- broadcasting ------------------------------------------------------

    async def _flush_loop(self) -> None:
        """Broadcast coalesced prices on a fixed cadence."""
        settings = get_settings()
        while self._running:
            await asyncio.sleep(settings.finnhub_stream_flush_seconds)
            if self._pending:
                await self.flush()

    async def flush(self) -> int:
        """Send one message per symbol that ticked. Returns the number sent."""
        batch, self._pending = self._pending, {}
        now = datetime.now(timezone.utc).isoformat()

        for symbol, price in batch.items():
            reference = self._reference.get(symbol)
            change = (
                round((price - reference) / reference * 100, 4)
                if reference
                else None
            )
            await ticker_hub.broadcast_price(
                symbol,
                {
                    "type": "price_update",
                    "ticker": symbol,
                    "price": price,
                    "change": change,
                    "timestamp": now,
                    # Lets the UI distinguish a live trade from a polled close.
                    "source": "stream",
                },
            )
        return len(batch)


finnhub_stream = FinnhubStream()
