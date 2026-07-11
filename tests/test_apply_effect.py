"""Tests for apply_effect."""

from __future__ import annotations

from typing import Any

from engine.apply import apply_effect
from engine.events import EventBus, GameEvent, HookContext
from models.effects import AddPointsOp, EffectProgram, ReverseOrderOp
from models.game_state import GameState, Player


class SpyBus(EventBus):
    """Records every emit call and returns state unchanged."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, list[str]]] = []

    def emit(self, event: GameEvent, state: Any, ctx: HookContext) -> Any:
        self.calls.append((str(event), list(ctx.target_player_ids)))
        return state


def _state() -> GameState:
    players = [Player(id="p1", name="A", score=10), Player(id="p2", name="B", score=5)]
    return GameState(room_code="AAAA", players=players)


def _ctx() -> HookContext:
    return HookContext(event=GameEvent.ON_PLAY, actor_id="p1")


def test_add_points_increases_score() -> None:
    st = _state()
    out = apply_effect(st, EffectProgram(ops=[AddPointsOp(amount=3)]), _ctx(), bus=SpyBus())
    assert out.get_player("p1").score == 13
    assert st.get_player("p1").score == 10  # original unchanged


def test_score_change_emitted_once_per_scoring_op() -> None:
    spy = SpyBus()
    prog = EffectProgram(ops=[AddPointsOp(amount=3), AddPointsOp(target="all", amount=1)])
    apply_effect(_state(), prog, _ctx(), bus=spy)
    assert len(spy.calls) == 2
    assert spy.calls[0][0] == "on_score_change"
    assert spy.calls[0][1] == ["p1"]
    assert set(spy.calls[1][1]) == {"p1", "p2"}


def test_non_score_op_does_not_emit() -> None:
    spy = SpyBus()
    apply_effect(_state(), EffectProgram(ops=[ReverseOrderOp()]), _ctx(), bus=spy)
    assert spy.calls == []


def test_empty_program_is_noop() -> None:
    spy = SpyBus()
    st = _state()
    out = apply_effect(st, EffectProgram(ops=[]), _ctx(), bus=spy)
    assert spy.calls == []
    assert out.get_player("p1").score == 10


def test_default_bus_with_empty_registry_is_noop_effect() -> None:
    # No bus injected -> uses module _bus -> fire_hooks with empty default
    # registry returns state unchanged (no hooks registered).
    st = _state()
    out = apply_effect(st, EffectProgram(ops=[AddPointsOp(amount=2)]), _ctx())
    assert out.get_player("p1").score == 12


def test_zero_amount_add_does_not_emit() -> None:
    # add_points with amount 0 changes no score -> no emit
    spy = SpyBus()
    apply_effect(_state(), EffectProgram(ops=[AddPointsOp(amount=0)]), _ctx(), bus=spy)
    assert spy.calls == []
