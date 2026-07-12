"""Tests for the turn loop: draw_step, advance_turn, run_turn."""

from __future__ import annotations

from typing import Any

from engine.apply import apply_effect  # noqa: F401  (ensures import graph is sound)
from engine.events import EventBus, GameEvent, HookContext
from engine.loop import advance_turn, draw_step, register_skip_predicate, run_turn
from models.effects import AddPointsOp, EffectProgram
from models.game_state import GameState, Player


class SpyBus(EventBus):
    def __init__(self) -> None:
        self.events: list[str] = []

    def emit(self, event: GameEvent, state: Any, ctx: HookContext) -> Any:
        self.events.append(str(event))
        return state


def _state(**kw) -> GameState:
    players = kw.pop("players", None) or [
        Player(id="p1", name="A", score=0, hand=[]),
        Player(id="p2", name="B", score=0, hand=[]),
        Player(id="p3", name="C", score=0, hand=[]),
    ]
    defaults = {"room_code": "AAAA", "players": players, "deck": ["d1", "d2", "d3", "d4"], "phase": "playing"}
    defaults.update(kw)
    return GameState(**defaults)


def test_draw_step_draws_draw_count() -> None:
    st = _state(draw_count=2)
    out = draw_step(st, "p1", bus=SpyBus())
    assert out.get_player("p1").hand == ["d1", "d2"]
    assert out.deck == ["d3", "d4"]
    assert st.deck == ["d1", "d2", "d3", "d4"]  # original unchanged


def test_draw_step_empty_deck_ends_game() -> None:
    st = _state(deck=[])
    out = draw_step(st, "p1", bus=SpyBus())
    assert out.phase == "ended"


def test_advance_turn_direction_1() -> None:
    st = _state(turn_index=0, direction=1)
    assert advance_turn(st).turn_index == 1


def test_advance_turn_direction_neg1() -> None:
    st = _state(turn_index=0, direction=-1)
    assert advance_turn(st).turn_index == 2  # (0 - 1) % 3


def test_skip_next_is_skipped_and_cleared() -> None:
    players = [
        Player(id="p1", name="A", score=0, hand=[]),
        Player(id="p2", name="B", score=0, hand=[], conditions={"skip_next": True}),
        Player(id="p3", name="C", score=0, hand=[]),
    ]
    st = _state(players=players, turn_index=0, direction=1)
    out = advance_turn(st)
    assert out.turn_index == 2  # p2 skipped -> lands on p3
    assert out.get_player("p2").conditions == {}
    assert st.get_player("p2").conditions == {"skip_next": True}  # original unchanged


def test_extra_turn_keeps_index_and_clears() -> None:
    players = [
        Player(id="p1", name="A", score=0, hand=[]),
        Player(id="p2", name="B", score=0, hand=[], conditions={"extra_turn": True}),
        Player(id="p3", name="C", score=0, hand=[]),
    ]
    st = _state(players=players, turn_index=1, direction=1)
    out = advance_turn(st)
    assert out.turn_index == 1  # stays on p2
    assert out.get_player("p2").conditions == {}
    assert st.get_player("p2").conditions == {"extra_turn": True}  # original unchanged


def test_skip_predicate_registry() -> None:
    register_skip_predicate("always_skip", lambda player, state: True)
    st = _state(turn_index=0, direction=1, skip_predicate="always_skip")
    out = advance_turn(st)
    # next is p2 (idx1); predicate true -> skip once more -> p3 (idx2)
    assert out.turn_index == 2


def test_skip_predicate_unknown_name_no_extra_skip() -> None:
    # skip_predicate names a predicate that is NOT registered -> pred_fn is None,
    # so no extra skip happens.
    st = _state(turn_index=0, direction=1, skip_predicate="not_registered")
    out = advance_turn(st)
    assert out.turn_index == 1  # plain advance to p2


def test_skip_predicate_returning_false_no_extra_skip() -> None:
    register_skip_predicate("never_skip", lambda player, state: False)
    st = _state(turn_index=0, direction=1, skip_predicate="never_skip")
    out = advance_turn(st)
    assert out.turn_index == 1  # predicate False -> no extra skip


def test_run_turn_fires_events() -> None:
    spy = SpyBus()

    def play_fn(state: GameState, pid: str):
        return state, EffectProgram(ops=[AddPointsOp(amount=1)]), HookContext(event=GameEvent.ON_PLAY, actor_id=pid)

    st = _state(turn_index=0, draw_count=1)
    out = run_turn(st, play_fn, bus=spy)
    assert "on_turn_start" in spy.events
    assert "on_turn_end" in spy.events
    assert "on_win_check" in spy.events
    assert out.get_player("p1").score == 1
    assert out.turn_index == 1


def test_run_turn_noop_when_ended() -> None:
    spy = SpyBus()
    st = _state(phase="ended")

    def play_fn(state: GameState, pid: str):
        raise AssertionError("play_fn should not be called")

    out = run_turn(st, play_fn, bus=spy)
    assert out is st
    assert spy.events == []
