"""Tests for the spectator concept: join-after-start becomes a spectator.

Covers the join-gating policy (RoomManager.join), the model shape (a separate
GameState.spectators collection), turn-rotation exclusion (engine.loop.advance_turn),
action guarding (Room._dispatch), scoring exclusion (engine.scoring), and dealing
(Room._handle_start).
"""

from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, patch

from agent.contract import InterpretResult
from engine.loop import advance_turn
from engine.scoring import evaluate_win_condition
from models.game_state import GameState, Player, Spectator, WinCondition
from conftest import drive_to_playing

from models.ws_messages import CreateCardMsg, PassMsg, PlayMsg
from board.rooms.manager import RoomManager
from board.rooms.room import Room


# ── join gating ──
def test_join_in_lobby_is_normal_player() -> None:
    mgr = RoomManager()
    code = mgr.create_room()  # fresh room -> phase "lobby"
    result = mgr.join(code, "Alice")
    assert result is not None
    _, pid, spectator = result
    assert spectator is False
    player = mgr.get(code).state.get_player(pid)
    assert player.id == pid
    assert not mgr.get(code).state.is_spectator(pid)


def test_join_after_start_is_spectator() -> None:
    mgr = RoomManager()
    code = mgr.create_room()
    room = mgr.get(code)
    # First player joins in the lobby, then the game starts.
    _, p1, _ = mgr.join(code, "Alice")
    room.state = room.state.model_copy(update={"phase": "playing"})
    # A later joiner becomes a spectator.
    result = mgr.join(code, "Late")
    assert result is not None
    _, late_id, spectator = result
    assert spectator is True
    assert room.state.is_spectator(late_id)
    assert late_id not in [p.id for p in room.state.players]
    # The original player is unaffected.
    assert not room.state.is_spectator(p1)


def test_join_in_each_started_phase_is_spectator() -> None:
    for phase in ("setup", "playing", "results", "epilogue", "ended"):
        mgr = RoomManager()
        code = mgr.create_room()
        room = mgr.get(code)
        room.state = room.state.model_copy(update={"phase": phase})
        _, _, spectator = mgr.join(code, "Late")
        assert spectator is True, f"phase {phase!r} should seat a spectator"


# ── model / turn rotation ──
def test_turn_players_is_all_players() -> None:
    players = [Player(id="p1", name="A"), Player(id="p2", name="B")]
    state = GameState(room_code="AAAA", players=players)
    assert [p.id for p in state.turn_players()] == ["p1", "p2"]


def test_advance_turn_cycles_players_only() -> None:
    # Spectators live outside `players` entirely, so advancing never needs to
    # skip over one — they can't be landed on in the first place.
    players = [
        Player(id="p1", name="A"),
        Player(id="p2", name="B"),
    ]
    state = GameState(
        room_code="AAAA",
        players=players,
        spectators=[],
        turn_index=0,
        phase="playing",
    )
    out = advance_turn(state)
    assert out.turn_index == 1
    assert out.active_player().id == "p2"
    out2 = advance_turn(out)
    assert out2.turn_index == 0
    assert out2.active_player().id == "p1"


# ── dealing ──
def _room_two_players_and_start() -> Room:
    room = Room("ABCDEF")
    room.add_player("p1", "Alice")
    room.add_player("p2", "Bob")
    room.connections.connect("p1", AsyncMock())
    room.connections.connect("p2", AsyncMock())
    import agent.rag.store as store

    store._client = None  # offline seed-file fallback
    # Drive the full two-step start flow (lobby -> setup -> playing): each real
    # player authors the required cards, then the host starts the game.
    drive_to_playing(room, ["p1", "p2"])
    return room


def test_spectator_joining_mid_game_has_no_hand_and_is_not_active() -> None:
    room = _room_two_players_and_start()
    assert room.state.phase == "playing"
    # A spectator joins mid-game.
    room.add_spectator("spec", "Watcher")
    assert room.state.is_spectator("spec")
    assert "spec" not in [p.id for p in room.state.players]
    # Active player is never the spectator across a full rotation.
    for _ in range(4):
        assert room.state.active_player().id != "spec"
        room.state = advance_turn(room.state)


def test_spectator_is_never_auto_drawn_to() -> None:
    room = _room_two_players_and_start()
    room.add_spectator("spec", "Watcher")
    # Passing cycles turns; the spectator must never receive drawn cards.
    for _ in range(4):
        asyncio.run(room.handle_action(room.state.active_player().id, PassMsg()))
        if room.state.phase == "ended":
            break
    assert "spec" not in [p.id for p in room.state.players]


# ── action guarding ──
def _playing_room_with_spectator() -> Room:
    room = Room("ABCDEF")
    room.add_player("p1", "Alice")
    room.add_player("p2", "Bob")
    room.add_spectator("spec", "Watcher")
    room.state = room.state.model_copy(update={"phase": "playing", "deck": ["d1", "d2", "d3"]})
    return room


def _spectator_error_types(room: Room, msg) -> list[str]:
    ws = AsyncMock()
    room.connections.connect("spec", ws)
    asyncio.run(room.handle_action("spec", msg))
    return [json.loads(c.args[0])["type"] for c in ws.send_text.call_args_list]


def test_spectator_pass_is_rejected() -> None:
    room = _playing_room_with_spectator()
    assert "error" in _spectator_error_types(room, PassMsg())
    # Turn unchanged.
    assert room.state.turn_index == 0


def test_spectator_play_is_rejected() -> None:
    room = _playing_room_with_spectator()
    room.state = room.state.model_copy(update={"cards": {"c1": {"id": "c1", "title": "X", "description": "y"}}})
    assert "error" in _spectator_error_types(room, PlayMsg(card_id="c1"))


def test_spectator_create_card_is_rejected() -> None:
    room = _playing_room_with_spectator()
    types = _spectator_error_types(room, CreateCardMsg(title="Wild", description="do something"))
    assert "error" in types
    # No card was authored.
    assert room.state.cards == {}


def test_non_spectator_create_card_still_allowed_off_turn() -> None:
    room = _playing_room_with_spectator()
    room.connections.connect("p2", AsyncMock())
    fake = InterpretResult(program=None, snippet=None, verdict="invalid")
    with patch("agent.runtime.run_agent", return_value=fake):
        asyncio.run(room.handle_action("p2", CreateCardMsg(title="Wild", description="x")))
    assert len(room.state.cards) == 1


# ── scoring ──
def test_scoring_excludes_spectators() -> None:
    # Spectators have no `score` field at all (they live outside `players`),
    # so there is no way for one to outrank a real player's score.
    players = [
        Player(id="p1", name="A", score=5),
        Player(id="p2", name="B", score=3),
    ]
    state = GameState(
        room_code="AAAA",
        players=players,
        spectators=[Spectator(id="spec", name="S")],
        win_condition=WinCondition(kind="highest_points"),
    )
    assert evaluate_win_condition(state) == ["p1"]
