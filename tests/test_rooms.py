"""Comprehensive tests for the rooms layer: RoomManager, Room, ConnectionManager."""

from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, patch

from conftest import drive_to_playing

from models.ws_messages import CreateCardMsg, DrawMsg, PassMsg, PlayMsg, Placement
from board.rooms.connections import ConnectionManager
from board.rooms.manager import RoomManager
from board.rooms.room import Room


def _room_two_players() -> Room:
    r = Room("ABCDEF")
    r.add_player("p1", "Alice")
    r.add_player("p2", "Bob")
    return r


# ─────────────────────────────────────────────────────────────────────────────
# RoomManager
# ─────────────────────────────────────────────────────────────────────────────


class TestRoomManager:
    def test_create_room_returns_6_char_alnum_code(self) -> None:
        mgr = RoomManager()
        code = mgr.create_room()
        assert isinstance(code, str)
        assert len(code) == 6
        assert code.isalnum()
        assert code.isupper() or code.isdigit() or any(ch.isdigit() for ch in code)

    def test_create_room_is_retrievable(self) -> None:
        mgr = RoomManager()
        code = mgr.create_room()
        room = mgr.get(code)
        assert isinstance(room, Room)
        assert room.code == code

    def test_join_returns_code_and_uuid_like_player_id(self) -> None:
        mgr = RoomManager()
        code = mgr.create_room()
        result = mgr.join(code, "Alice")
        assert result is not None
        returned_code, player_id, spectator = result
        assert returned_code == code
        assert spectator is False  # room still in lobby -> normal player
        # UUID-ish: 36 chars, 4 dashes, hex groups
        assert len(player_id) == 36
        assert player_id.count("-") == 4
        # the player is now on the room
        assert player_id in mgr.get(code).get_player_ids()

    def test_join_missing_room_returns_none(self) -> None:
        mgr = RoomManager()
        assert mgr.join("NOPE00", "Alice") is None

    def test_get_is_case_insensitive(self) -> None:
        mgr = RoomManager()
        code = mgr.create_room()  # always uppercase
        assert mgr.get(code.lower()) is mgr.get(code)
        assert mgr.get(code.lower()) is not None

    def test_get_missing_returns_none(self) -> None:
        mgr = RoomManager()
        assert mgr.get("ZZZZZZ") is None

    def test_codes_are_unique_across_many_creates(self) -> None:
        mgr = RoomManager()
        codes = {mgr.create_room() for _ in range(200)}
        assert len(codes) == 200

    def test_join_multiple_players_distinct_ids(self) -> None:
        mgr = RoomManager()
        code = mgr.create_room()
        _, id1, _ = mgr.join(code, "Alice")
        _, id2, _ = mgr.join(code, "Bob")
        assert id1 != id2
        assert mgr.get(code).get_player_ids() == [id1, id2]


# ─────────────────────────────────────────────────────────────────────────────
# Room — turn enforcement (async via asyncio.run)
# ─────────────────────────────────────────────────────────────────────────────


class TestRoomTurnEnforcement:
    def test_pass_off_turn_sends_error_and_leaves_turn_unchanged(self) -> None:
        room = _room_two_players()
        room.state = room.state.model_copy(update={"deck": ["c1", "c2"], "phase": "playing"})
        ws2 = AsyncMock()
        room.connections.connect("p2", ws2)  # p2 is NOT active (turn_index 0 -> p1)
        asyncio.run(room.handle_action("p2", PassMsg()))
        sent_types = [json.loads(c.args[0])["type"] for c in ws2.send_text.call_args_list]
        assert "error" in sent_types
        assert room.state.turn_index == 0
        assert room.state.deck == ["c1", "c2"]

    def test_pass_advances_turn_and_next_player_must_draw(self) -> None:
        room = _room_two_players()
        room.state = room.state.model_copy(update={"deck": ["c1", "c2"], "phase": "playing"})
        ws1, ws2 = AsyncMock(), AsyncMock()
        room.connections.connect("p1", ws1)
        room.connections.connect("p2", ws2)
        # p1 draws first (draw→play→end model), then ends their turn.
        asyncio.run(room.handle_action("p1", DrawMsg()))
        asyncio.run(room.handle_action("p1", PassMsg()))
        # Turn moved to p2. There is NO auto-draw at turn start: p2's hand is
        # untouched and has_drawn resets so they must draw themselves.
        assert room.state.turn_index == 1
        assert room.state.deck == ["c2"]  # only p1's draw removed a card
        assert room.state.get_player("p2").hand == []
        assert room.state.get_player("p1").hand == ["c1"]  # p1 drew, kept it
        assert room._has_drawn is False
        ws1.send_text.assert_called()
        ws2.send_text.assert_called()
        # p2 then draws explicitly and gets the next card.
        asyncio.run(room.handle_action("p2", DrawMsg()))
        assert room.state.get_player("p2").hand == ["c2"]
        assert room.state.deck == []

    def test_pass_on_empty_deck_opens_epilogue(self) -> None:
        room = _room_two_players()
        # Model "the last card was already drawn earlier this game": deck empty
        # and exhaustion latched, so p1 passing ends their turn and the game,
        # which now resolves end-of-game and opens the epilogue vote.
        room.state = room.state.model_copy(update={"deck": [], "phase": "playing"})
        room._deck_exhausted = True
        ws1 = AsyncMock()
        room.connections.connect("p1", ws1)
        asyncio.run(room.handle_action("p1", PassMsg()))
        assert room.state.phase == "epilogue"

    def test_start_sets_phase_playing(self) -> None:
        room = _room_two_players()
        room.connections.connect("p1", AsyncMock())
        room.connections.connect("p2", AsyncMock())
        # Full two-step flow: lobby -> setup (author 5 each) -> playing.
        drive_to_playing(room, ["p1", "p2"])
        assert room.state.phase == "playing"

    def test_create_card_off_turn_allowed(self) -> None:
        room = _room_two_players()
        room.state = room.state.model_copy(update={"phase": "playing"})
        room.connections.connect("p2", AsyncMock())  # p2 is off-turn
        with patch(
            "agent.graph.interpret_card",
            return_value={"program": None, "snippet": None, "verdict": "invalid"},
        ):
            asyncio.run(room.handle_action("p2", CreateCardMsg(title="Wild", description="do stuff")))
        assert len(room.state.cards) == 1
        card = next(iter(room.state.cards.values()))
        assert card["title"] == "Wild"
        assert card["creator_id"] == "p2"

    def test_play_off_turn_sends_error_and_never_reaches_agent(self) -> None:
        room = _room_two_players()
        # give p1 (active) an existing card so the off-turn path is purely turn-gated
        room.state = room.state.model_copy(
            update={
                "phase": "playing",
                "cards": {"card-x": {"id": "card-x", "title": "T", "description": "D"}},
            }
        )
        ws2 = AsyncMock()
        room.connections.connect("p2", ws2)  # p2 off-turn
        msg = PlayMsg(card_id="card-x", placement=Placement(zone="self"))
        # patch defensively: it must NOT be called on the off-turn path
        with patch("agent.graph.interpret_card") as mock_interpret:
            asyncio.run(room.handle_action("p2", msg))
        mock_interpret.assert_not_called()
        sent = json.loads(ws2.send_text.call_args.args[0])
        assert sent["type"] == "error"
        assert "turn" in sent["message"].lower()


# ─────────────────────────────────────────────────────────────────────────────
# ConnectionManager (async via asyncio.run)
# ─────────────────────────────────────────────────────────────────────────────


class TestConnectionManager:
    def test_broadcast_reaches_all_connected_sockets(self) -> None:
        cm = ConnectionManager()
        ws1, ws2 = AsyncMock(), AsyncMock()
        cm.connect("p1", ws1)
        cm.connect("p2", ws2)
        asyncio.run(cm.broadcast({"type": "ping"}))
        expected = json.dumps({"type": "ping"})
        ws1.send_text.assert_called_once_with(expected)
        ws2.send_text.assert_called_once_with(expected)

    def test_broadcast_state_wraps_in_state_envelope(self) -> None:
        cm = ConnectionManager()
        ws = AsyncMock()
        cm.connect("p1", ws)
        asyncio.run(cm.broadcast_state({"players": [], "phase": "lobby"}))
        expected = json.dumps({"type": "state", "state": {"players": [], "phase": "lobby"}})
        ws.send_text.assert_called_once_with(expected)

    def test_send_targets_one_player_only(self) -> None:
        cm = ConnectionManager()
        ws1, ws2 = AsyncMock(), AsyncMock()
        cm.connect("p1", ws1)
        cm.connect("p2", ws2)
        asyncio.run(cm.send("p1", {"type": "hi"}))
        ws1.send_text.assert_called_once_with(json.dumps({"type": "hi"}))
        ws2.send_text.assert_not_called()

    def test_send_missing_player_is_noop(self) -> None:
        cm = ConnectionManager()
        # must not raise
        asyncio.run(cm.send("ghost", {"type": "hi"}))
        assert cm.connected_players == []

    def test_failed_send_disconnects_that_player(self) -> None:
        cm = ConnectionManager()
        ws = AsyncMock()
        ws.send_text.side_effect = RuntimeError("socket closed")
        cm.connect("p1", ws)
        asyncio.run(cm.send("p1", {"type": "x"}))
        assert "p1" not in cm.connected_players

    def test_failed_broadcast_disconnects_only_dead_socket(self) -> None:
        cm = ConnectionManager()
        ws_ok, ws_bad = AsyncMock(), AsyncMock()
        ws_bad.send_text.side_effect = RuntimeError("socket closed")
        cm.connect("ok", ws_ok)
        cm.connect("bad", ws_bad)
        asyncio.run(cm.broadcast({"type": "x"}))
        assert cm.connected_players == ["ok"]

    def test_disconnect_removes_player(self) -> None:
        cm = ConnectionManager()
        cm.connect("p1", AsyncMock())
        assert "p1" in cm.connected_players
        cm.disconnect("p1")
        assert "p1" not in cm.connected_players

    def test_disconnect_missing_player_is_noop(self) -> None:
        cm = ConnectionManager()
        cm.disconnect("ghost")  # must not raise
        assert cm.connected_players == []
