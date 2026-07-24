"""Condition TTLs (bead tr7.2): duration_turns on set_condition, ticked at the
owner's turn start. The tick that reaches 0 keeps the condition active through
that whole turn (its last); the NEXT tick removes it — so duration_turns=N is
active for exactly N of the owner's turns and ON_TURN_START hooks see it N times.
"""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock

from engine.compile import compile_card
from engine.events import EventBus, GameEvent, HookContext
from engine.loop import run_turn, tick_condition_ttls
from engine.reducers import apply_op
from engine.sandbox.api_surface import SandboxGame
from models.effects import AddPointsOp, EffectProgram, SetConditionOp
from models.game_state import GameState, Player
from board.rooms.redaction import redact_snapshot
from board.rooms.room import Room


def _state(**kw) -> GameState:
    players = kw.pop("players", None) or [
        Player(id="p1", name="Alice"),
        Player(id="p2", name="Bob"),
    ]
    defaults = {"room_code": "TTLS", "players": players, "deck": ["d1", "d2", "d3", "d4"], "phase": "playing"}
    defaults.update(kw)
    return GameState(**defaults)


def _ctx(actor_id: str = "p1") -> HookContext:
    return HookContext(event=GameEvent.ON_PLAY, actor_id=actor_id)


def _set(state: GameState, key: str, value: Any = True, duration: int | None = None, target: str = "self"):
    op = SetConditionOp(target=target, key=key, value=value, duration_turns=duration)
    return apply_op(state, op, _ctx("p1"))


class TestSetConditionDuration:
    def test_duration_stores_ttl_alongside_condition(self):
        st = _set(_state(), "poisoned", value=2, duration=3)
        p1 = st.get_player("p1")
        assert p1.conditions["poisoned"] == 2
        assert p1.condition_ttls["poisoned"] == 3

    def test_no_duration_stores_no_ttl(self):
        st = _set(_state(), "cursed")
        p1 = st.get_player("p1")
        assert p1.conditions["cursed"] is True
        assert "cursed" not in p1.condition_ttls

    def test_overwrite_with_new_duration_resets_ttl(self):
        st = _set(_state(), "poisoned", duration=3)
        st = tick_condition_ttls(st, "p1")
        assert st.get_player("p1").condition_ttls["poisoned"] == 2
        st = _set(st, "poisoned", duration=3)
        assert st.get_player("p1").condition_ttls["poisoned"] == 3

    def test_overwrite_without_duration_clears_stale_ttl(self):
        st = _set(_state(), "poisoned", duration=2)
        st = _set(st, "poisoned")
        p1 = st.get_player("p1")
        assert p1.conditions["poisoned"] is True
        assert p1.condition_ttls == {}
        for _ in range(5):
            st = tick_condition_ttls(st, "p1")
        assert st.get_player("p1").conditions["poisoned"] is True

    def test_value_none_removes_condition_and_ttl(self):
        st = _set(_state(), "poisoned", duration=3)
        st = _set(st, "poisoned", value=None)
        p1 = st.get_player("p1")
        assert "poisoned" not in p1.conditions
        assert "poisoned" not in p1.condition_ttls


class TestTick:
    def test_tick_decrements_only_the_owner(self):
        st = _set(_state(), "poisoned", duration=2)
        st = tick_condition_ttls(st, "p2")
        assert st.get_player("p1").condition_ttls["poisoned"] == 2
        st = tick_condition_ttls(st, "p1")
        assert st.get_player("p1").condition_ttls["poisoned"] == 1

    def test_final_tick_keeps_condition_active_at_ttl_zero(self):
        st = _set(_state(), "poisoned", duration=1)
        st = tick_condition_ttls(st, "p1")
        p1 = st.get_player("p1")
        assert p1.conditions["poisoned"] is True
        assert p1.condition_ttls == {"poisoned": 0}

    def test_tick_past_zero_removes_condition_and_ttl_entry(self):
        st = _set(_state(), "poisoned", duration=1)
        st = tick_condition_ttls(st, "p1")
        st = tick_condition_ttls(st, "p1")
        p1 = st.get_player("p1")
        assert "poisoned" not in p1.conditions
        assert p1.condition_ttls == {}

    def test_conditions_without_ttl_persist_forever(self):
        st = _set(_state(), "cursed")
        for _ in range(10):
            st = tick_condition_ttls(st, "p1")
        assert st.get_player("p1").conditions["cursed"] is True

    def test_tick_without_ttls_returns_state_unchanged(self):
        st = _state()
        assert tick_condition_ttls(st, "p1") is st

    def test_mixed_ttl_and_permanent_conditions(self):
        st = _set(_set(_state(), "cursed"), "poisoned", duration=1)
        st = tick_condition_ttls(st, "p1")
        st = tick_condition_ttls(st, "p1")
        p1 = st.get_player("p1")
        assert p1.conditions == {"cursed": True}
        assert p1.condition_ttls == {}


class TestRunTurnIntegration:
    def test_ttl_ticks_at_owner_turn_start_not_others(self):
        class SpyBus(EventBus):
            def emit(self, event, state, ctx):
                return state

        def play_fn(state: GameState, pid: str):
            return state, EffectProgram(ops=[AddPointsOp(amount=1)]), _ctx(pid)

        st = _set(_state(turn_index=0, deck=[f"d{i}" for i in range(10)]), "poisoned", duration=2, target="id:p2")
        st = run_turn(st, play_fn, bus=SpyBus())  # p1's turn: p2's TTL untouched
        assert st.get_player("p2").condition_ttls["poisoned"] == 2
        st = run_turn(st, play_fn, bus=SpyBus())  # p2's 1st turn ticks it, stays active
        assert st.get_player("p2").condition_ttls["poisoned"] == 1
        st = run_turn(st, play_fn, bus=SpyBus())  # p1 again: no tick for p2
        assert st.get_player("p2").condition_ttls["poisoned"] == 1
        st = run_turn(st, play_fn, bus=SpyBus())  # p2's 2nd (last active) turn: ttl 0, still set
        assert st.get_player("p2").conditions["poisoned"] is True
        assert st.get_player("p2").condition_ttls["poisoned"] == 0
        st = run_turn(st, play_fn, bus=SpyBus())  # p1 again
        st = run_turn(st, play_fn, bus=SpyBus())  # p2's 3rd turn start removes it
        assert "poisoned" not in st.get_player("p2").conditions
        assert st.get_player("p2").condition_ttls == {}

    def test_turn_start_hooks_see_condition_on_exactly_duration_turns(self):
        class TurnStartSpyBus(EventBus):
            def __init__(self):
                super().__init__()
                self.poisoned_starts = 0

            def emit(self, event, state, ctx):
                if event == GameEvent.ON_TURN_START and ctx.actor_id == "p2":
                    if state.get_player("p2").conditions.get("poisoned"):
                        self.poisoned_starts += 1
                return state

        def play_fn(state: GameState, pid: str):
            return state, EffectProgram(ops=[AddPointsOp(amount=1)]), _ctx(pid)

        for duration in (1, 2):
            bus = TurnStartSpyBus()
            st = _set(
                _state(turn_index=0, deck=[f"d{i}" for i in range(10)]),
                "poisoned",
                duration=duration,
                target="id:p2",
            )
            for _ in range(2 * duration + 4):
                st = run_turn(st, play_fn, bus=bus)
                if st.phase == "ended":
                    break
            assert bus.poisoned_starts == duration


class TestRoomStartTurn:
    def test_start_turn_ticks_and_expires(self):
        room = Room("ABCDEF")
        room.add_player("p1", "Alice")
        room.add_player("p2", "Bob")
        room.state = room.state.model_copy(update={"deck": ["c1", "c2", "c3", "c4"], "phase": "playing"})
        room.state = room.state.with_condition("p1", "poisoned", True, ttl=2)
        room.connections.connect("p1", AsyncMock())
        asyncio.run(room._start_turn("p1"))
        assert room.state.get_player("p1").condition_ttls["poisoned"] == 1
        assert room.state.get_player("p1").conditions["poisoned"] is True
        asyncio.run(room._start_turn("p1"))
        assert room.state.get_player("p1").condition_ttls["poisoned"] == 0
        assert room.state.get_player("p1").conditions["poisoned"] is True
        asyncio.run(room._start_turn("p1"))
        assert "poisoned" not in room.state.get_player("p1").conditions
        assert room.state.get_player("p1").condition_ttls == {}

    def test_start_turn_leaves_other_players_ttls_alone(self):
        room = Room("ABCDEF")
        room.add_player("p1", "Alice")
        room.add_player("p2", "Bob")
        room.state = room.state.model_copy(update={"deck": ["c1", "c2"], "phase": "playing"})
        room.state = room.state.with_condition("p2", "poisoned", True, ttl=1)
        room.connections.connect("p1", AsyncMock())
        asyncio.run(room._start_turn("p1"))
        assert room.state.get_player("p2").condition_ttls["poisoned"] == 1


class TestSerializationAndRedaction:
    def test_condition_ttls_round_trip(self):
        st = _set(_state(), "poisoned", value={"stacks": 2}, duration=4)
        revived = GameState(**st.model_dump())
        assert revived.get_player("p1").condition_ttls == {"poisoned": 4}
        assert revived.get_player("p1").conditions == {"poisoned": {"stacks": 2}}
        via_json = GameState.model_validate_json(st.model_dump_json())
        assert via_json.get_player("p1").condition_ttls == {"poisoned": 4}

    def test_redaction_leaves_condition_ttls_intact(self):
        st = _set(_state(), "poisoned", duration=3)
        snap = st.model_dump()
        for viewer in ("p1", "p2", None):
            view = redact_snapshot(snap, viewer)
            p1 = next(p for p in view["players"] if p["id"] == "p1")
            assert p1["condition_ttls"] == {"poisoned": 3}
            assert p1["conditions"] == {"poisoned": True}


class TestCompileAndSandbox:
    def test_compile_lowers_duration_turns(self):
        card = {
            "id": "c1",
            "title": "Poison",
            "ops": [{"op": "set_condition", "args": {"key": "poisoned", "value": True, "duration_turns": 3}}],
        }
        prog = compile_card(card)
        assert prog is not None
        op = prog.ops[0]
        assert isinstance(op, SetConditionOp)
        assert op.duration_turns == 3

    def test_compile_defaults_duration_to_none(self):
        card = {"id": "c1", "title": "Curse", "ops": [{"op": "set_condition", "args": {"key": "cursed"}}]}
        prog = compile_card(card)
        assert prog.ops[0].duration_turns is None

    def test_api_surface_records_duration_only_when_given(self):
        g = SandboxGame({}, {})
        g.set_condition("id:p2", "poisoned", 2, duration_turns=3)
        g.set_condition("id:p2", "cursed")
        recorded = g._ops
        assert recorded[0]["duration_turns"] == 3
        assert "duration_turns" not in recorded[1]
