"""Tests for engine reducers and target resolution."""

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


def _state() -> GameState:
    players = [
        Player(id="p1", name="A", score=10, hand=["x"]),
        Player(id="p2", name="B", score=5, hand=[]),
        Player(id="p3", name="C", score=20, hand=["y", "z"]),
    ]
    return GameState(room_code="AAAA", players=players, deck=["d1", "d2", "d3"], direction=1)


def _ctx(actor: str = "p1", chosen: str | None = None) -> HookContext:
    return HookContext(event=GameEvent.ON_PLAY, actor_id=actor, chosen_player_id=chosen)


def test_resolve_self() -> None:
    assert _resolve_targets("self", _ctx(), _state()) == ["p1"]


def test_resolve_neighbors_direction_1() -> None:
    st = _state()
    assert _resolve_targets("right_neighbor", _ctx("p1"), st) == ["p2"]
    assert _resolve_targets("left_neighbor", _ctx("p1"), st) == ["p3"]


def test_resolve_neighbors_direction_neg1() -> None:
    st = _state().model_copy(update={"direction": -1})
    assert _resolve_targets("right_neighbor", _ctx("p1"), st) == ["p3"]
    assert _resolve_targets("left_neighbor", _ctx("p1"), st) == ["p2"]


def test_resolve_all_and_others() -> None:
    st = _state()
    assert set(_resolve_targets("all", _ctx("p1"), st)) == {"p1", "p2", "p3"}
    assert set(_resolve_targets("all_others", _ctx("p1"), st)) == {"p2", "p3"}


def test_resolve_most_least_points() -> None:
    st = _state()
    assert _resolve_targets("player_with_most_points", _ctx(), st) == ["p3"]
    assert _resolve_targets("player_with_least_points", _ctx(), st) == ["p2"]


def test_resolve_empty_hand() -> None:
    assert _resolve_targets("player_with_empty_hand", _ctx(), _state()) == ["p2"]


def test_resolve_chooser_requires_choice() -> None:
    with pytest.raises(ValueError):
        _resolve_targets("chooser", _ctx(), _state())
    assert _resolve_targets("chooser", _ctx(chosen="p2"), _state()) == ["p2"]


def test_resolve_target_player() -> None:
    assert _resolve_targets("target_player", _ctx(chosen="p3"), _state()) == ["p3"]


def test_resolve_unknown_target_raises() -> None:
    with pytest.raises(ValueError):
        _resolve_targets("nonsense", _ctx(), _state())  # type: ignore[arg-type]


def test_add_points_pure() -> None:
    st = _state()
    new = apply_op(st, AddPointsOp(amount=5), _ctx("p1"))
    assert new.get_player("p1").score == 15
    assert st.get_player("p1").score == 10  # original unchanged


def test_add_points_all() -> None:
    st = _state()
    new = apply_op(st, AddPointsOp(target="all", amount=1), _ctx("p1"))
    assert [p.score for p in new.players] == [11, 6, 21]


def test_subtract_and_set_points() -> None:
    st = _state()
    assert apply_op(st, SubtractPointsOp(amount=3), _ctx("p1")).get_player("p1").score == 7
    assert apply_op(st, SetPointsOp(amount=0), _ctx("p1")).get_player("p1").score == 0


def test_reverse_order() -> None:
    st = _state()
    assert apply_op(st, ReverseOrderOp(), _ctx()).direction == -1
    assert st.direction == 1


def test_change_draw_count() -> None:
    assert apply_op(_state(), ChangeDrawCountOp(amount=3), _ctx()).draw_count == 3


def test_skip_turn_does_not_mutate_original() -> None:
    st = _state()
    new = apply_op(st, SkipTurnOp(target="self"), _ctx("p1"))
    assert "p1" in new._skip_next
    assert "p1" not in st._skip_next


def test_extra_turn() -> None:
    st = _state()
    new = apply_op(st, ExtraTurnOp(target="self"), _ctx("p1"))
    assert "p1" in new._extra_turn
    assert "p1" not in st._extra_turn


def test_steal_points_capped_at_target_score() -> None:
    st = _state()
    # p1 steals 100 from p2 (who has 5) -> only 5 moves
    new = apply_op(st, StealPointsOp(from_target="right_neighbor", to_target="self", amount=100), _ctx("p1"))
    assert new.get_player("p2").score == 0
    assert new.get_player("p1").score == 15


def test_steal_points_partial() -> None:
    st = _state()
    new = apply_op(st, StealPointsOp(from_target="left_neighbor", to_target="self", amount=3), _ctx("p1"))
    assert new.get_player("p3").score == 17
    assert new.get_player("p1").score == 13


def test_draw_cards() -> None:
    st = _state()
    new = apply_op(st, DrawCardsOp(target="self", amount=2), _ctx("p1"))
    assert new.get_player("p1").hand == ["x", "d1", "d2"]
    assert new.deck == ["d3"]


def test_destroy_card() -> None:
    st = _state()
    new = apply_op(st, DestroyCardOp(card_id="y"), _ctx("p1"))
    assert "y" not in new.get_player("p3").hand
    assert "y" in new.discard


def test_destroy_card_no_dup_in_discard() -> None:
    st = _state().model_copy(update={"discard": ["y"]})
    new = apply_op(st, DestroyCardOp(card_id="y"), _ctx("p1"))
    assert new.discard.count("y") == 1


def test_set_win_condition() -> None:
    st = _state()
    new = apply_op(st, SetWinConditionOp(kind="lowest_points"), _ctx())
    assert new.win_condition.kind == "lowest_points"


def test_custom_note_logs() -> None:
    st = _state()
    new = apply_op(st, CustomNoteOp(note="hi"), _ctx())
    assert any("hi" in line for line in new.log)
    assert st.log == []
