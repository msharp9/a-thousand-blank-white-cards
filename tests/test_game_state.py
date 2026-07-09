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


# ── card zones ──
def test_in_play_defaults_empty() -> None:
    p = Player(id="p1", name="A")
    assert p.in_play == []


def test_cards_in_play_empty_by_default() -> None:
    state = GameState(room_code="AAAA", players=[Player(id="p1", name="A")])
    assert state.cards_in_play() == []
    assert state.cards_in_play_for("p1") == []
    assert state.center_cards() == []


def test_cards_in_play_aggregates_in_player_order() -> None:
    players = [
        Player(id="p1", name="A", in_play=["c1", "c2"]),
        Player(id="p2", name="B", in_play=["c3"]),
    ]
    state = GameState(room_code="AAAA", players=players)
    assert state.cards_in_play() == ["c1", "c2", "c3"]


def test_cards_in_play_for_single_player() -> None:
    players = [
        Player(id="p1", name="A", in_play=["c1"]),
        Player(id="p2", name="B", in_play=["c2", "c3"]),
    ]
    state = GameState(room_code="AAAA", players=players)
    assert state.cards_in_play_for("p2") == ["c2", "c3"]
    # Returned list is a copy — mutating it does not affect state.
    got = state.cards_in_play_for("p2")
    got.append("x")
    assert state.get_player("p2").in_play == ["c2", "c3"]


def test_center_cards_reads_house_rules() -> None:
    state = GameState(room_code="AAAA", house_rules=["hr1", "hr2"])
    assert state.center_cards() == ["hr1", "hr2"]
    # Copy, not alias.
    state.center_cards().append("x")
    assert state.house_rules == ["hr1", "hr2"]


def test_move_card_hand_to_in_play_is_immutable() -> None:
    state = GameState(room_code="AAAA", players=[Player(id="p1", name="A", hand=["c1", "c2"])])
    new = state.move_card("c1", "hand", "in_play", from_player_id="p1", to_player_id="p1")
    assert new.get_player("p1").hand == ["c2"]
    assert new.get_player("p1").in_play == ["c1"]
    # Source untouched.
    assert state.get_player("p1").hand == ["c1", "c2"]
    assert state.get_player("p1").in_play == []
    assert new is not state


def test_move_card_in_play_to_center() -> None:
    state = GameState(room_code="AAAA", players=[Player(id="p1", name="A", in_play=["c1"])])
    new = state.move_card("c1", "in_play", "center", from_player_id="p1")
    assert new.get_player("p1").in_play == []
    assert new.center_cards() == ["c1"]


def test_move_card_in_play_to_discard() -> None:
    state = GameState(room_code="AAAA", players=[Player(id="p1", name="A", in_play=["c1"])])
    new = state.move_card("c1", "in_play", "discard", from_player_id="p1")
    assert new.get_player("p1").in_play == []
    assert new.discard == ["c1"]


def test_move_card_between_players_in_play() -> None:
    players = [Player(id="p1", name="A", in_play=["c1"]), Player(id="p2", name="B")]
    state = GameState(room_code="AAAA", players=players)
    new = state.move_card("c1", "in_play", "in_play", from_player_id="p1", to_player_id="p2")
    assert new.get_player("p1").in_play == []
    assert new.get_player("p2").in_play == ["c1"]


def test_move_card_requires_player_id_for_player_zone() -> None:
    state = GameState(room_code="AAAA", players=[Player(id="p1", name="A", hand=["c1"])])
    with pytest.raises(ValueError):
        state.move_card("c1", "hand", "center")
