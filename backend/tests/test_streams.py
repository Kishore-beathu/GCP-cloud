"""TickerHub subscription bookkeeping and broadcast fan-out."""

from __future__ import annotations

import pytest

from app.services.streams import TickerHub

pytestmark = pytest.mark.asyncio


class FakeSocket:
    """Stands in for a WebSocket: records messages, optionally fails on send."""

    def __init__(self, fail: bool = False) -> None:
        self.sent: list[dict] = []
        self.fail = fail
        self.accepted = False

    async def accept(self) -> None:
        self.accepted = True

    async def send_json(self, message: dict) -> None:
        if self.fail:
            raise RuntimeError("peer gone")
        self.sent.append(message)


async def test_connect_subscribes_to_initial_ticker():
    hub = TickerHub()
    socket = FakeSocket()

    await hub.connect(socket, "mrna")

    assert socket.accepted
    assert hub.subscribed_tickers() == {"MRNA"}


async def test_price_broadcast_reaches_only_subscribers():
    hub = TickerHub()
    mrna_socket, pfe_socket = FakeSocket(), FakeSocket()
    await hub.connect(mrna_socket, "MRNA")
    await hub.connect(pfe_socket, "PFE")

    await hub.broadcast_price("MRNA", {"type": "price_update", "ticker": "MRNA"})

    assert len(mrna_socket.sent) == 1
    assert pfe_socket.sent == []


async def test_alert_broadcast_reaches_every_socket():
    hub = TickerHub()
    sockets = [FakeSocket(), FakeSocket()]
    await hub.connect(sockets[0], "MRNA")
    await hub.connect(sockets[1], "PFE")

    await hub.broadcast_alert({"headline": "FDA approves"})

    for socket in sockets:
        assert socket.sent == [{"type": "alert", "headline": "FDA approves"}]


async def test_unsubscribe_stops_delivery():
    hub = TickerHub()
    socket = FakeSocket()
    await hub.connect(socket, "MRNA")
    await hub.subscribe(socket, ["PFE"])

    remaining = await hub.unsubscribe(socket, ["MRNA"])
    assert remaining == {"PFE"}

    await hub.broadcast_price("MRNA", {"type": "price_update", "ticker": "MRNA"})
    assert socket.sent == []


async def test_dead_socket_is_reaped_on_send_failure():
    hub = TickerHub()
    dead, alive = FakeSocket(fail=True), FakeSocket()
    await hub.connect(dead, "MRNA")
    await hub.connect(alive, "MRNA")

    await hub.broadcast_price("MRNA", {"type": "price_update", "ticker": "MRNA"})

    # The healthy socket still got the message; the dead one is gone from the hub.
    assert len(alive.sent) == 1
    await hub.broadcast_price("MRNA", {"type": "price_update", "ticker": "MRNA"})
    assert len(alive.sent) == 2


async def test_disconnect_clears_all_state():
    hub = TickerHub()
    socket = FakeSocket()
    await hub.connect(socket, "MRNA")

    await hub.disconnect(socket)

    assert hub.subscribed_tickers() == set()
