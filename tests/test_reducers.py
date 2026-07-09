"""Full unit tests for engine reducers and _resolve_targets."""

from __future__ import annotations

import pytest

from tbwc.engine.events import GameEvent, HookContext
from tbwc.engine.reducers import _resolve_targets, apply_op
from tbwc.models.effects import (
    AddPointsOp,
    ChangeDrawCountOp,
    CustomNoteOp,
    DestroyCardOp,
    DrawCardsOp,
    ExtraTurnOp,
    ReverseOrderOp,
    SetPointsOp,
    SetWinConditionOp,
    SkipTurnOp,
    StealPointsOp,
    SubtractPointsOp,
)
from tbwc.models.game_state import GameState, Player


def make_state(players=None, deck=None, direction=1, draw_count=1) -> GameState:
    if players is None:
        players = [
            Player(id="p1", name="Alice", score=10, hand=["c1", "c2"]),
            Player(id="p2", name="Bob", score=5, hand=["c3"]),
            Player(id="p3", name="Carol", score=20, hand=[]),
        ]
    return GameState(
        room_code="TEST",
        players=players,
        deck=deck or ["d1", "d2", "d3"],
        direction=direction,
        draw_count=draw_count,
        turn_index=0,
    )


def make_ctx(actor_id="p1", chosen=None) -> HookContext:
    return HookContext(event=GameEvent.ON_PLAY, actor_id=actor_id, chosen_player_id=chosen)


class TestResolveTargets:
    def test_self(self):
        assert _resolve_targets("self", make_ctx("p1"), make_state()) == ["p1"]

    def test_right_neighbor_clockwise(self):
        assert _resolve_targets("right_neighbor", make_ctx("p1"), make_state()) == ["p2"]

    def test_left_neighbor_clockwise(self):
        assert _resolve_targets("left_neighbor", make_ctx("p1"), make_state()) == ["p3"]

    def test_right_neighbor_counter_clockwise(self):
        assert _resolve_targets("right_neighbor", make_ctx("p1"), make_state(direction=-1)) == ["p3"]

    def test_all(self):
        assert set(_resolve_targets("all", make_ctx("p1"), make_state())) == {"p1", "p2", "p3"}

    def test_all_others(self):
        assert set(_resolve_targets("all_others", make_ctx("p1"), make_state())) == {"p2", "p3"}

    def test_chooser_requires_ctx(self):
        with pytest.raises(ValueError):
            _resolve_targets("chooser", make_ctx("p1", chosen=None), make_state())

    def test_chooser_with_ctx(self):
        assert _resolve_targets("chooser", make_ctx("p1", chosen="p2"), make_state()) == ["p2"]

    def test_player_with_most_points(self):
        assert _resolve_targets("player_with_most_points", make_ctx("p1"), make_state()) == ["p3"]

    def test_player_with_least_points(self):
        assert _resolve_targets("player_with_least_points", make_ctx("p1"), make_state()) == ["p2"]

    def test_player_with_empty_hand(self):
        assert _resolve_targets("player_with_empty_hand", make_ctx("p1"), make_state()) == ["p3"]

    def test_unknown_target_raises(self):
        with pytest.raises(ValueError):
            _resolve_targets("not_a_real_target", make_ctx("p1"), make_state())


class TestSkipTurn:
    def test_marks_target_and_leaves_original_unchanged(self):
        state = make_state()
        ctx = make_ctx("p1", chosen="p2")
        new = apply_op(state, SkipTurnOp(target="target_player"), ctx)
        assert new._skip_next == {"p2"}
        assert state._skip_next == set()  # original untouched

    def test_marks_multiple_targets(self):
        state = make_state()
        new = apply_op(state, SkipTurnOp(target="all_others"), make_ctx("p1"))
        assert new._skip_next == {"p2", "p3"}
        assert state._skip_next == set()


class TestExtraTurn:
    def test_marks_target_and_leaves_original_unchanged(self):
        state = make_state()
        new = apply_op(state, ExtraTurnOp(target="self"), make_ctx("p1"))
        assert new._extra_turn == {"p1"}
        assert state._extra_turn == set()  # original untouched

    def test_marks_multiple_targets(self):
        state = make_state()
        new = apply_op(state, ExtraTurnOp(target="all"), make_ctx("p1"))
        assert new._extra_turn == {"p1", "p2", "p3"}
        assert state._extra_turn == set()


class TestAddPoints:
    def test_adds_to_self(self):
        state = make_state()
        new = apply_op(state, AddPointsOp(amount=5), make_ctx("p1"))
        assert new.get_player("p1").score == 15
        assert state.get_player("p1").score == 10  # immutable

    def test_adds_to_all(self):
        new = apply_op(make_state(), AddPointsOp(target="all", amount=3), make_ctx("p1"))
        assert new.get_player("p1").score == 13
        assert new.get_player("p2").score == 8
        assert new.get_player("p3").score == 23


class TestSubtractPoints:
    def test_subtracts_from_target(self):
        ctx = make_ctx("p1", chosen="p2")
        new = apply_op(make_state(), SubtractPointsOp(target="target_player", amount=3), ctx)
        assert new.get_player("p2").score == 2


class TestSetPoints:
    def test_sets_exact_value(self):
        new = apply_op(make_state(), SetPointsOp(target="self", amount=0), make_ctx("p1"))
        assert new.get_player("p1").score == 0


class TestReverseOrder:
    def test_flips_direction(self):
        assert apply_op(make_state(direction=1), ReverseOrderOp(), make_ctx("p1")).direction == -1

    def test_double_reverse(self):
        new = apply_op(make_state(direction=1), ReverseOrderOp(), make_ctx("p1"))
        new2 = apply_op(new, ReverseOrderOp(), make_ctx("p1"))
        assert new2.direction == 1


class TestChangeDrawCount:
    def test_sets_draw_count(self):
        assert apply_op(make_state(), ChangeDrawCountOp(amount=3), make_ctx("p1")).draw_count == 3


class TestStealPoints:
    def test_transfers_points(self):
        ctx = make_ctx("p1", chosen="p2")
        new = apply_op(make_state(), StealPointsOp(from_target="target_player", to_target="self", amount=3), ctx)
        assert new.get_player("p2").score == 2
        assert new.get_player("p1").score == 13

    def test_cannot_steal_below_zero(self):
        ctx = make_ctx("p1", chosen="p2")
        new = apply_op(make_state(), StealPointsOp(from_target="target_player", to_target="self", amount=100), ctx)
        assert new.get_player("p2").score == 0
        assert new.get_player("p1").score == 15  # only stole 5


class TestDrawCards:
    def test_draws_from_deck(self):
        new = apply_op(make_state(deck=["d1", "d2", "d3"]), DrawCardsOp(target="self", amount=2), make_ctx("p1"))
        assert "d1" in new.get_player("p1").hand
        assert "d2" in new.get_player("p1").hand
        assert new.deck == ["d3"]


class TestDestroyCard:
    def test_removes_from_hand(self):
        new = apply_op(make_state(), DestroyCardOp(card_id="c1"), make_ctx("p1"))
        assert "c1" not in new.get_player("p1").hand
        assert "c1" in new.discard


class TestSetWinCondition:
    def test_sets_kind_and_threshold(self):
        new = apply_op(make_state(), SetWinConditionOp(kind="first_to", threshold=50), make_ctx("p1"))
        assert new.win_condition.kind == "first_to"
        assert new.win_condition.threshold == 50


class TestCustomNote:
    def test_appends_log(self):
        new = apply_op(make_state(), CustomNoteOp(note="hello"), make_ctx("p1"))
        assert any("hello" in entry for entry in new.log)
