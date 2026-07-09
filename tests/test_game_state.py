"""Tests for GameState, Player, WinCondition."""

from __future__ import annotations

import pytest

from tbwc.models.game_state import GameState, Player, WinCondition


def test_constructs_with_defaults() -> None:
    state = GameState(room_code="AAAA")
    assert state.room_code == "AAAA"
    assert state.players == []
    assert state.direction == 1
    assert state.draw_count == 1
    assert state.phase == "lobby"
    assert isinstance(state.win_condition, WinCondition)
    assert state.win_condition.kind == "highest_points"


def test_active_player_respects_turn_index() -> None:
    players = [Player(id="p1", name="A"), Player(id="p2", name="B"), Player(id="p3", name="C")]
    state = GameState(room_code="AAAA", players=players, turn_index=1)
    assert state.active_player().id == "p2"
    state2 = GameState(room_code="AAAA", players=players, turn_index=4)
    assert state2.active_player().id == "p2"  # 4 % 3 == 1


def test_get_player_found_and_missing() -> None:
    state = GameState(room_code="AAAA", players=[Player(id="p1", name="A")])
    assert state.get_player("p1").name == "A"
    with pytest.raises(KeyError):
        state.get_player("nope")


def test_with_log_is_immutable() -> None:
    state = GameState(room_code="AAAA")
    new = state.with_log("hello")
    assert new.log == ["hello"]
    assert state.log == []  # original unchanged
    assert new is not state


def test_private_attrs_default() -> None:
    state = GameState(room_code="AAAA")
    assert state._skip_next == set()
    assert state._extra_turn == set()
    state._skip_next.add("p1")
    assert "p1" in state._skip_next


def test_copy_with_turn_flags_rebinds_both_sets() -> None:
    state = GameState(room_code="AAAA", turn_index=1)
    state._skip_next = {"p1"}
    state._extra_turn = {"p2"}
    new = state.copy_with_turn_flags()
    # Values default to copies of the current values...
    assert new._skip_next == {"p1"}
    assert new._extra_turn == {"p2"}
    # ...but BOTH are fresh objects, never shared with the source.
    assert new._skip_next is not state._skip_next
    assert new._extra_turn is not state._extra_turn
    # turn_index unchanged when not provided.
    assert new.turn_index == 1


def test_copy_with_turn_flags_updates_index_and_sets() -> None:
    state = GameState(room_code="AAAA", turn_index=0)
    state._skip_next = {"a"}
    state._extra_turn = {"b"}
    new = state.copy_with_turn_flags(turn_index=2, skip_next={"x"}, extra_turn={"y"})
    assert new.turn_index == 2
    assert new._skip_next == {"x"}
    assert new._extra_turn == {"y"}
    # A provided set is copied, not aliased.
    assert new._skip_next is not state._skip_next
    # Source is untouched.
    assert state._skip_next == {"a"}
    assert state._extra_turn == {"b"}
