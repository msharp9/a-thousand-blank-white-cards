"""Tests for ConnectionManager."""

from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock

from board.rooms.connections import ConnectionManager


def test_broadcast() -> None:
    cm = ConnectionManager()
    ws1, ws2 = AsyncMock(), AsyncMock()
    cm.connect("p1", ws1)
    cm.connect("p2", ws2)
    asyncio.run(cm.broadcast({"type": "ping"}))
    ws1.send_text.assert_called_once_with(json.dumps({"type": "ping"}))
    ws2.send_text.assert_called_once_with(json.dumps({"type": "ping"}))


def test_broadcast_state_wraps_envelope() -> None:
    cm = ConnectionManager()
    ws = AsyncMock()
    cm.connect("p1", ws)
    asyncio.run(cm.broadcast_state({"players": []}))
    ws.send_text.assert_called_once_with(json.dumps({"type": "state", "state": {"players": []}}))


def test_send_to_single_player() -> None:
    cm = ConnectionManager()
    ws1, ws2 = AsyncMock(), AsyncMock()
    cm.connect("p1", ws1)
    cm.connect("p2", ws2)
    asyncio.run(cm.send("p1", {"type": "hi"}))
    ws1.send_text.assert_called_once()
    ws2.send_text.assert_not_called()


def test_send_missing_player_is_noop() -> None:
    cm = ConnectionManager()
    asyncio.run(cm.send("ghost", {"type": "hi"}))  # must not raise


def test_failed_send_disconnects() -> None:
    cm = ConnectionManager()
    ws = AsyncMock()
    ws.send_text.side_effect = RuntimeError("socket closed")
    cm.connect("p1", ws)
    asyncio.run(cm.send("p1", {"type": "x"}))
    assert "p1" not in cm.connected_players


def test_failed_broadcast_disconnects() -> None:
    cm = ConnectionManager()
    ws_ok, ws_bad = AsyncMock(), AsyncMock()
    ws_bad.send_text.side_effect = RuntimeError("socket closed")
    cm.connect("ok", ws_ok)
    cm.connect("bad", ws_bad)
    asyncio.run(cm.broadcast({"type": "x"}))
    assert cm.connected_players == ["ok"]


def test_disconnect_removes_player() -> None:
    cm = ConnectionManager()
    cm.connect("p1", AsyncMock())
    cm.disconnect("p1")
    assert "p1" not in cm.connected_players


def test_disconnect_with_stale_socket_keeps_replacement() -> None:
    cm = ConnectionManager()
    old, new = AsyncMock(), AsyncMock()
    cm.connect("p1", old)
    cm.connect("p1", new)
    cm.disconnect("p1", old)
    assert cm.get("p1") is new
    cm.disconnect("p1", new)
    assert cm.get("p1") is None


def test_get_returns_registered_socket() -> None:
    cm = ConnectionManager()
    ws = AsyncMock()
    cm.connect("p1", ws)
    assert cm.get("p1") is ws
    assert cm.get("ghost") is None
