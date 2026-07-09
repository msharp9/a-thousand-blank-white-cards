"""Tests for the Room class (turn enforcement + state mutation)."""

from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, patch

from tbwc.models.ws_messages import CreateCardMsg, DrawMsg, StartMsg
from tbwc.rooms.room import Room


def _room_with_two_players() -> Room:
    room = Room("ABCDEF")
    room.add_player("p1", "Alice")
    room.add_player("p2", "Bob")
    return room


def test_room_constructs() -> None:
    room = Room("ABCDEF")
    assert room.code == "ABCDEF"
    assert room.state.room_code == "ABCDEF"
    assert room.get_player_ids() == []


def test_add_player_is_immutable_reassign() -> None:
    room = _room_with_two_players()
    assert room.get_player_ids() == ["p1", "p2"]


def test_draw_off_turn_sends_error() -> None:
    room = _room_with_two_players()
    room.state = room.state.model_copy(update={"deck": ["c1", "c2"], "phase": "playing"})
    ws2 = AsyncMock()
    room.connections.connect("p2", ws2)  # p2 is NOT active (turn_index 0 -> p1)
    asyncio.run(room.handle_action("p2", DrawMsg()))
    # p2 got an error, deck unchanged
    ws2.send_text.assert_called_once()
    sent = json.loads(ws2.send_text.call_args.args[0])
    assert sent["type"] == "error"
    assert room.state.deck == ["c1", "c2"]


def test_draw_on_turn_draws_and_broadcasts() -> None:
    room = _room_with_two_players()
    room.state = room.state.model_copy(update={"deck": ["c1", "c2"], "phase": "playing"})
    ws1, ws2 = AsyncMock(), AsyncMock()
    room.connections.connect("p1", ws1)
    room.connections.connect("p2", ws2)
    asyncio.run(room.handle_action("p1", DrawMsg()))
    # p1 drew c1; deck now [c2]; both got a state broadcast
    assert room.state.deck == ["c2"]
    assert "c1" in room.state.get_player("p1").hand
    ws1.send_text.assert_called()
    ws2.send_text.assert_called()


def test_start_sets_phase_playing() -> None:
    room = _room_with_two_players()
    room.connections.connect("p1", AsyncMock())
    asyncio.run(room.handle_action("p1", StartMsg()))
    assert room.state.phase == "playing"


def test_create_card_off_turn_allowed() -> None:
    room = _room_with_two_players()
    room.state = room.state.model_copy(update={"phase": "playing"})
    room.connections.connect("p2", AsyncMock())
    fake_result = {"program": None, "snippet": None, "verdict": "invalid"}
    with patch("tbwc.agent.graph.interpret_card", return_value=fake_result):
        asyncio.run(room.handle_action("p2", CreateCardMsg(title="Wild", description="do something")))
    assert len(room.state.cards) == 1
