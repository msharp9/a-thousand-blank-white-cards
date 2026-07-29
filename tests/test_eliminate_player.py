"""eliminate_player (bead agd.3): reducer, turn loop, scoring, sandbox facade, compile."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

from board.rooms.room import Room
from engine.compile import compile_card
from engine.events import GameEvent, HookContext
from engine.loop import advance_turn
from engine.reducers import apply_op
from engine.sandbox.api_surface import SandboxGame
from engine.scoring import evaluate_end_condition, evaluate_win_condition, win_condition_met
from models.effects import EliminatePlayerOp
from models.game_state import GameState, Player
from models.ws_messages import PlayMsg


def _players() -> list[Player]:
    return [
        Player(id="p1", name="A", hand=["a1", "a2"], in_play=["ap1"]),
        Player(id="p2", name="B", hand=["b1"]),
        Player(id="p3", name="C", hand=["c1"]),
    ]


def _state(players: list[Player] | None = None, **kw) -> GameState:
    defaults = {
        "room_code": "TEST",
        "players": players if players is not None else _players(),
        "deck": ["d1", "d2"],
        "phase": "playing",
    }
    defaults.update(kw)
    return GameState(**defaults)


def _ctx(actor: str = "p1", chosen: str | None = None) -> HookContext:
    return HookContext(event=GameEvent.ON_PLAY, actor_id=actor, chosen_player_id=chosen)


class TestReducer:
    def test_sets_flag_discards_hand_keeps_in_play(self):
        state = _state()
        out = apply_op(state, EliminatePlayerOp(target="id:p1"), _ctx("p2"))
        p1 = out.get_player("p1")
        assert p1.eliminated is True
        assert p1.hand == []
        assert p1.in_play == ["ap1"]  # table cards keep working
        assert out.discard == ["a1", "a2"]
        assert not state.get_player("p1").eliminated  # original untouched
        assert state.get_player("p1").hand == ["a1", "a2"]

    def test_chooser_target_resolves(self):
        out = apply_op(_state(), EliminatePlayerOp(target="chooser"), _ctx("p1", chosen="p3"))
        assert out.get_player("p3").eliminated is True
        assert not out.get_player("p1").eliminated

    def test_last_player_guard(self):
        players = _players()
        players[1] = players[1].model_copy(update={"eliminated": True, "hand": []})
        players[2] = players[2].model_copy(update={"eliminated": True, "hand": []})
        state = _state(players)
        out = apply_op(state, EliminatePlayerOp(target="id:p1"), _ctx("p2"))
        assert out.get_player("p1").eliminated is False
        assert out.get_player("p1").hand == ["a1", "a2"]
        assert any("last player standing" in line for line in out.log)

    def test_eliminate_all_leaves_exactly_one_standing(self):
        out = apply_op(_state(), EliminatePlayerOp(target="all"), _ctx("p1"))
        survivors = [p.id for p in out.players if not p.eliminated]
        assert survivors == ["p3"]  # targets resolve in order; the last one is guarded

    def test_already_eliminated_is_skipped(self):
        once = apply_op(_state(), EliminatePlayerOp(target="id:p2"), _ctx("p1"))
        twice = apply_op(once, EliminatePlayerOp(target="id:p2"), _ctx("p1"))
        assert twice.discard == ["b1"]  # hand not re-discarded
        assert twice.log == once.log


class TestTurnLoop:
    def test_advance_skips_eliminated_player(self):
        players = _players()
        players[1] = players[1].model_copy(update={"eliminated": True, "hand": []})
        out = advance_turn(_state(players, turn_index=0))
        assert out.active_player().id == "p3"

    def test_advance_from_eliminated_active_player(self):
        players = _players()
        players[0] = players[0].model_copy(update={"eliminated": True, "hand": []})
        out = advance_turn(_state(players, turn_index=0))
        assert out.active_player().id == "p2"

    def test_extra_turn_consumed_but_not_honoured_when_eliminated(self):
        players = _players()
        players[0] = players[0].model_copy(update={"eliminated": True, "hand": [], "conditions": {"extra_turn": True}})
        out = advance_turn(_state(players, turn_index=0))
        assert out.active_player().id == "p2"
        assert out.get_player("p1").conditions == {}

    def test_skip_next_consumed_while_stepping_over_eliminated(self):
        players = _players()
        players[1] = players[1].model_copy(update={"eliminated": True, "hand": []})
        players[2] = players[2].model_copy(update={"conditions": {"skip_next": True}})
        out = advance_turn(_state(players, turn_index=0))
        assert out.active_player().id == "p1"  # p2 eliminated, p3 skipped -> wraps home
        assert out.get_player("p3").conditions == {}

    def test_lone_survivor_keeps_the_turn(self):
        players = _players()
        players[1] = players[1].model_copy(update={"eliminated": True, "hand": []})
        players[2] = players[2].model_copy(update={"eliminated": True, "hand": []})
        out = advance_turn(_state(players, turn_index=0))
        assert out.active_player().id == "p1"


class TestScoring:
    def test_last_standing_fires_when_one_remains(self):
        state = _state(win_condition={"kind": "last_standing"})
        state = apply_op(state, EliminatePlayerOp(target="all_others"), _ctx("p1"))
        assert evaluate_win_condition(state) == ["p1"]
        assert win_condition_met(state) is True

    def test_last_standing_silent_with_two_remaining(self):
        state = _state(win_condition={"kind": "last_standing"})
        state = apply_op(state, EliminatePlayerOp(target="id:p3"), _ctx("p1"))
        assert evaluate_win_condition(state) == []
        assert win_condition_met(state) is False

    def test_eliminated_player_cannot_win_on_points(self):
        players = _players()
        players[0] = players[0].model_copy(update={"score": 5})
        players[1] = players[1].model_copy(update={"score": 99, "eliminated": True, "hand": []})
        state = _state(players)
        assert evaluate_win_condition(state) == ["p1"]

    def test_empty_hand_end_condition_ignores_eliminated_hands(self):
        state = _state(rules={"end_condition": {"type": "empty_hand"}})
        state = apply_op(state, EliminatePlayerOp(target="id:p2"), _ctx("p1"))
        assert evaluate_end_condition(state) is False


def test_sandbox_facade_records_op() -> None:
    game = SandboxGame(
        {"players": [{"id": "p1", "name": "A", "score": 0, "hand": []}], "turn_index": 0},
        {"actor_id": "p1"},
    )
    game.eliminate_player("id:p2")
    assert game.ops() == [{"op": "eliminate_player", "target": "id:p2"}]


def test_sandbox_player_view_exposes_eliminated() -> None:
    game = SandboxGame(
        {
            "players": [
                {"id": "p1", "name": "A", "score": 0, "hand": []},
                {"id": "p2", "name": "B", "score": 0, "hand": [], "eliminated": True},
            ],
            "turn_index": 0,
        },
        {"actor_id": "p1"},
    )
    assert game.player("p1").eliminated is False
    assert game.player("p2").eliminated is True


class TestCompile:
    def test_maps_authoring_op(self):
        program = compile_card({"ops": [{"op": "eliminate_player", "args": {"target": "player"}}]})
        assert program is not None
        assert program.ops == [EliminatePlayerOp(target="chooser")]
        assert program.requires_choice is True

    def test_default_target_is_chooser(self):
        program = compile_card({"ops": [{"op": "eliminate_player", "args": {}}]})
        assert program is not None
        assert program.ops == [EliminatePlayerOp(target="chooser")]


class TestRoomTurnStart:
    """A hook that eliminates the incoming active player ends that turn
    immediately: no auto-draw for them, and the turn passes straight to the
    next survivor instead of leaving an eliminated player 'on the clock'."""

    @staticmethod
    def _room(hook_event: str) -> "Room":
        code = "def apply(state, ctx):\n    state.eliminate_player('id:p2')\n"
        landmine = {
            "id": "landmine",
            "title": "Landmine",
            "description": "",
            "canonical": {"ops": [{"op": "register_hook", "args": {"event": hook_event, "code": code}}]},
        }
        room = Room("ELIMTS")
        room.add_player("p1", "A")
        room.add_player("p2", "B")
        room.add_player("p3", "C")
        players = [p.model_copy(update={"hand": ["landmine"] if p.id == "p1" else []}) for p in room.state.players]
        room.state = room.state.model_copy(
            update={
                "phase": "playing",
                "deck": ["d1", "d2", "d3"],
                "cards": {"landmine": landmine},
                "players": players,
            }
        )
        room._has_drawn = True
        for pid in ("p1", "p2", "p3"):
            room.connections.connect(pid, AsyncMock())
        return room

    def test_on_turn_start_elimination_skips_to_the_next_survivor(self):
        room = self._room("on_turn_start")

        asyncio.run(room.handle_action("p1", PlayMsg(card_id="landmine")))

        p2 = room.state.get_player("p2")
        assert p2.eliminated is True
        assert p2.hand == []  # never auto-drew
        assert room.state.active_player().id == "p3"
        assert "d1" in room.state.get_player("p3").hand

    def test_on_draw_step_elimination_skips_to_the_next_survivor(self):
        room = self._room("on_draw_step")

        asyncio.run(room.handle_action("p1", PlayMsg(card_id="landmine")))

        p2 = room.state.get_player("p2")
        assert p2.eliminated is True
        assert p2.hand == []  # the drawn card was discarded with the hand
        assert room.state.active_player().id == "p3"
