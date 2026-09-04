"""Finnhub live trade stream: coalescing, subscriptions, and the full loop.

The end-to-end tests run a real WebSocket server on localhost and point the
client at it, so the connect / subscribe / tick / broadcast path is genuinely
exercised rather than mocked away.
"""

from __future__ import annotations

import asyncio
import contextlib
import json

import pytest
import websockets

from app.config import get_settings
from app.integrations.finnhub_stream import FinnhubStream


@pytest.fixture(autouse=True)
def _clear_settings_cache():
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


class RecordingHub:
    """Stands in for ticker_hub, capturing broadcasts."""

    def __init__(self) -> None:
        self.messages: list[tuple[str, dict]] = []

    async def broadcast_price(self, ticker: str, message: dict) -> None:
        self.messages.append((ticker, message))


# --- Coalescing and change maths --------------------------------------------


@pytest.mark.asyncio
async def test_flush_sends_one_message_per_symbol(monkeypatch):
    stream = FinnhubStream()
    hub = RecordingHub()
    monkeypatch.setattr("app.integrations.finnhub_stream.ticker_hub", hub)

    # Three ticks for MRNA; only the last price should reach the browser.
    stream._pending = {"MRNA": 101.0, "PFE": 30.0}
    stream._pending["MRNA"] = 103.5
    stream._reference = {"MRNA": 100.0}

    sent = await stream.flush()
    assert sent == 2

    by_ticker = {ticker: message for ticker, message in hub.messages}
    assert by_ticker["MRNA"]["price"] == 103.5
    assert by_ticker["MRNA"]["change"] == pytest.approx(3.5)
    assert by_ticker["MRNA"]["source"] == "stream"
    # No reference close: the price still flows, the change is simply unknown.
    assert by_ticker["PFE"]["change"] is None


@pytest.mark.asyncio
async def test_flush_clears_pending(monkeypatch):
    stream = FinnhubStream()
    monkeypatch.setattr("app.integrations.finnhub_stream.ticker_hub", RecordingHub())
    stream._pending = {"MRNA": 100.0}

    assert await stream.flush() == 1
    assert await stream.flush() == 0


def test_live_symbols_reports_streamed_prices():
    stream = FinnhubStream()
    assert stream.live_symbols() == set()
    stream._latest = {"MRNA": 100.0, "PFE": 30.0}
    assert stream.live_symbols() == {"MRNA", "PFE"}


def test_status_shape():
    stream = FinnhubStream()
    assert stream.status() == {
        "enabled": False,
        "connected": False,
        "subscribed": [],
        "live_prices": 0,
    }


# --- Lifecycle guards --------------------------------------------------------


@pytest.mark.asyncio
async def test_start_is_noop_without_api_key():
    stream = FinnhubStream()
    await stream.start()
    assert stream.status()["enabled"] is False
    await stream.stop()


@pytest.mark.asyncio
async def test_start_is_noop_when_disabled(monkeypatch):
    monkeypatch.setenv("FINNHUB_API_KEY", "key")
    monkeypatch.setenv("FINNHUB_STREAM_ENABLED", "false")
    get_settings.cache_clear()

    stream = FinnhubStream()
    await stream.start()
    assert stream.status()["enabled"] is False
    await stream.stop()


# --- End to end against a real WebSocket server ------------------------------


@pytest.mark.asyncio
async def test_streams_ticks_from_a_live_server(monkeypatch):
    """Connect, subscribe on demand, receive a trade, and broadcast it."""
    received_subscriptions: list[str] = []
    ready = asyncio.Event()

    async def server_handler(connection):
        async for raw in connection:
            message = json.loads(raw)
            if message.get("type") == "subscribe":
                received_subscriptions.append(message["symbol"])
                ready.set()
                # Answer the subscription with a trade tick.
                await connection.send(
                    json.dumps(
                        {
                            "type": "trade",
                            "data": [
                                {"s": message["symbol"], "p": 142.5, "t": 1, "v": 10},
                                {"s": message["symbol"], "p": 143.0, "t": 2, "v": 5},
                            ],
                        }
                    )
                )

    async with websockets.serve(server_handler, "127.0.0.1", 0) as server:
        port = server.sockets[0].getsockname()[1]

        monkeypatch.setenv("FINNHUB_API_KEY", "test-key")
        monkeypatch.setenv("FINNHUB_STREAM_FLUSH_SECONDS", "0.05")
        monkeypatch.setenv("FINNHUB_STREAM_RESYNC_SECONDS", "0.05")
        get_settings.cache_clear()
        monkeypatch.setattr("app.integrations.finnhub_stream.WS_URL", f"ws://127.0.0.1:{port}")

        # Demand drives subscriptions, so declare a watcher.
        hub = RecordingHub()
        hub.subscribed_tickers = lambda: {"MRNA"}  # type: ignore[attr-defined]
        monkeypatch.setattr("app.integrations.finnhub_stream.ticker_hub", hub)

        stream = FinnhubStream()
        # No database in this test; skip the reference-close lookup.
        stream._load_reference_closes = _noop  # type: ignore[method-assign]

        await stream.start()
        try:
            await asyncio.wait_for(ready.wait(), timeout=5)
            # Give the flush loop a moment to broadcast.
            for _ in range(50):
                if hub.messages:
                    break
                await asyncio.sleep(0.05)
        finally:
            await stream.stop()

    assert received_subscriptions == ["MRNA"]
    assert hub.messages, "expected a broadcast from the streamed tick"
    ticker, message = hub.messages[-1]
    assert ticker == "MRNA"
    # Coalesced to the newest of the two ticks.
    assert message["price"] == 143.0
    assert message["source"] == "stream"


@pytest.mark.asyncio
async def test_unsubscribes_when_viewers_leave(monkeypatch):
    """Dropping the last viewer releases the upstream subscription slot."""
    actions: list[tuple[str, str]] = []
    subscribed = asyncio.Event()
    unsubscribed = asyncio.Event()

    async def server_handler(connection):
        async for raw in connection:
            message = json.loads(raw)
            actions.append((message["type"], message["symbol"]))
            if message["type"] == "subscribe":
                subscribed.set()
            else:
                unsubscribed.set()

    async with websockets.serve(server_handler, "127.0.0.1", 0) as server:
        port = server.sockets[0].getsockname()[1]

        monkeypatch.setenv("FINNHUB_API_KEY", "test-key")
        monkeypatch.setenv("FINNHUB_STREAM_RESYNC_SECONDS", "0.05")
        get_settings.cache_clear()
        monkeypatch.setattr("app.integrations.finnhub_stream.WS_URL", f"ws://127.0.0.1:{port}")

        hub = RecordingHub()
        watched = {"MRNA"}
        hub.subscribed_tickers = lambda: set(watched)  # type: ignore[attr-defined]
        monkeypatch.setattr("app.integrations.finnhub_stream.ticker_hub", hub)

        stream = FinnhubStream()
        stream._load_reference_closes = _noop  # type: ignore[method-assign]
        stream._latest["MRNA"] = 100.0

        await stream.start()
        try:
            await asyncio.wait_for(subscribed.wait(), timeout=5)
            watched.clear()  # the viewer disconnects
            await asyncio.wait_for(unsubscribed.wait(), timeout=5)
        finally:
            await stream.stop()

    assert ("subscribe", "MRNA") in actions
    assert ("unsubscribe", "MRNA") in actions
    # The stale price is dropped so the polling job resumes covering the symbol.
    assert "MRNA" not in stream.live_symbols()


@pytest.mark.asyncio
async def test_reconnects_after_the_server_drops(monkeypatch):
    """A dropped connection is retried rather than ending the stream."""
    connections = 0
    second_connection = asyncio.Event()

    async def server_handler(connection):
        nonlocal connections
        connections += 1
        if connections == 1:
            await connection.close()  # hang up immediately
            return
        second_connection.set()
        await asyncio.sleep(5)

    async with websockets.serve(server_handler, "127.0.0.1", 0) as server:
        port = server.sockets[0].getsockname()[1]

        monkeypatch.setenv("FINNHUB_API_KEY", "test-key")
        monkeypatch.setenv("FINNHUB_STREAM_BACKOFF_SECONDS", "0.05")
        monkeypatch.setenv("FINNHUB_STREAM_RESYNC_SECONDS", "0.05")
        get_settings.cache_clear()
        monkeypatch.setattr("app.integrations.finnhub_stream.WS_URL", f"ws://127.0.0.1:{port}")

        hub = RecordingHub()
        hub.subscribed_tickers = lambda: set()  # type: ignore[attr-defined]
        monkeypatch.setattr("app.integrations.finnhub_stream.ticker_hub", hub)

        stream = FinnhubStream()
        stream._load_reference_closes = _noop  # type: ignore[method-assign]

        await stream.start()
        try:
            await asyncio.wait_for(second_connection.wait(), timeout=5)
        finally:
            await stream.stop()

    assert connections >= 2


async def _noop(*args, **kwargs) -> None:
    return None


# --- Interaction with the polling job ---------------------------------------


@pytest.mark.asyncio
async def test_price_push_job_skips_streamed_symbols(
    monkeypatch, session_factory, db, seeded_stocks
):
    """The poll must not overwrite a live trade price with a stored close."""
    from app import scheduler

    # The job opens its own session; point it at the test database.
    monkeypatch.setattr(scheduler, "get_session_factory", lambda: session_factory)

    pushed: list[str] = []

    class Hub:
        def subscribed_tickers(self):
            return {"MRNA", "PFE"}

        async def broadcast_price(self, ticker, message):
            pushed.append(ticker)

    class Stream:
        def live_symbols(self):
            return {"MRNA"}

    monkeypatch.setattr(scheduler, "ticker_hub", Hub())
    monkeypatch.setattr(scheduler, "finnhub_stream", Stream())

    await scheduler.price_push_job()

    # MRNA is streamed, so only PFE may be polled (and only if it has prices).
    assert "MRNA" not in pushed


# --- Refused handshakes -------------------------------------------------------
# A rejected handshake was caught by the catch-all, which logged a traceback for
# a routine rate limit and reconnected from the same two-second backoff whatever
# the reason — so a 429 was answered by the behaviour that caused it, and a bad
# key was retried forever.


def _rejecting_server(status: int, headers: dict[str, str] | None = None):
    """A server that refuses the WebSocket upgrade with a given HTTP status."""
    from http import HTTPStatus

    def process_request(connection, request):
        return connection.respond(HTTPStatus(status), "nope\n")

    async def handler(connection):  # pragma: no cover - never reached
        await connection.wait_closed()

    return handler, process_request


@pytest.mark.asyncio
async def test_a_rate_limited_handshake_waits_far_longer_than_the_normal_backoff(
    monkeypatch, caplog
):
    """Reconnecting two seconds after a 429 is what sustains the 429."""
    handler, process_request = _rejecting_server(429)

    async with websockets.serve(
        handler, "127.0.0.1", 0, process_request=process_request
    ) as server:
        port = server.sockets[0].getsockname()[1]

        monkeypatch.setenv("FINNHUB_API_KEY", "test-key")
        monkeypatch.setenv("FINNHUB_STREAM_RATE_LIMIT_BACKOFF_SECONDS", "45")
        get_settings.cache_clear()
        monkeypatch.setattr("app.integrations.finnhub_stream.WS_URL", f"ws://127.0.0.1:{port}")

        slept: list[float] = []

        async def _record_sleep(seconds):
            slept.append(seconds)
            raise asyncio.CancelledError  # stop after the first backoff

        monkeypatch.setattr(asyncio, "sleep", _record_sleep)

        stream = FinnhubStream()
        stream._load_reference_closes = _noop  # type: ignore[method-assign]
        stream._running = True
        with contextlib.suppress(asyncio.CancelledError):
            await stream._run()

    assert slept == [45.0], "a 429 must not be retried on the ordinary backoff"
    assert "rate limited" in caplog.text.lower()
    # A traceback for a documented vendor response is noise, not a diagnosis.
    assert "Traceback" not in caplog.text


@pytest.mark.asyncio
async def test_a_rejected_token_stops_the_stream_instead_of_retrying(monkeypatch, caplog):
    """Reconnecting cannot fix a wrong key, and every attempt spends quota."""
    handler, process_request = _rejecting_server(401)

    async with websockets.serve(
        handler, "127.0.0.1", 0, process_request=process_request
    ) as server:
        port = server.sockets[0].getsockname()[1]

        monkeypatch.setenv("FINNHUB_API_KEY", "wrong-key")
        get_settings.cache_clear()
        monkeypatch.setattr("app.integrations.finnhub_stream.WS_URL", f"ws://127.0.0.1:{port}")

        attempts = 0
        original_connect = websockets.connect

        def counting_connect(*args, **kwargs):
            nonlocal attempts
            attempts += 1
            return original_connect(*args, **kwargs)

        monkeypatch.setattr(websockets, "connect", counting_connect)

        stream = FinnhubStream()
        stream._load_reference_closes = _noop  # type: ignore[method-assign]
        stream._running = True
        await asyncio.wait_for(stream._run(), timeout=5)

    assert attempts == 1
    assert stream.status()["enabled"] is False
    assert "FINNHUB_API_KEY" in caplog.text
