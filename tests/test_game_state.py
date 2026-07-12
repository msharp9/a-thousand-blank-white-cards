"""Tests for GameState, Player, WinCondition."""

from __future__ import annotations

import pytest

from models.game_state import GameState, Player, WinCondition


def test_constructs_with_defaults() -> None:
    state = GameState(room_code="AAAA")
    assert state.room_code == "AAAA"
    assert state.players == []
    assert state.turn_order == []
    assert state.draw_count == 1
    assert state.phase == "lobby"
    assert isinstance(state.win_condition, WinCondition)
    assert state.win_condition.kind == "highest_points"


def test_effective_turn_order_falls_back_to_turn_players() -> None:
    players = [Player(id="p1", name="A"), Player(id="s1", name="S", spectator=True), Player(id="p2", name="B")]
    state = GameState(room_code="AAAA", players=players)
    assert state.effective_turn_order() == ["p1", "p2"]


def test_effective_turn_order_uses_explicit_list_when_set() -> None:
    players = [Player(id="p1", name="A"), Player(id="p2", name="B")]
    state = GameState(room_code="AAAA", players=players, turn_order=["p2", "p1"])
    assert state.effective_turn_order() == ["p2", "p1"]


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


def test_conditions_default_empty() -> None:
    state = GameState(room_code="AAAA", players=[Player(id="p1", name="A")])
    assert state.get_player("p1").conditions == {}


def test_with_condition_sets_key_immutably() -> None:
    state = GameState(room_code="AAAA", players=[Player(id="p1", name="A"), Player(id="p2", name="B")])
    new = state.with_condition("p1", "skip_next", True)
    assert new.get_player("p1").conditions == {"skip_next": True}
    assert new.get_player("p2").conditions == {}
    # Source untouched.
    assert state.get_player("p1").conditions == {}
    assert new is not state


def test_with_condition_preserves_other_keys() -> None:
    state = GameState(room_code="AAAA", players=[Player(id="p1", name="A", conditions={"poisoned": 2})])
    new = state.with_condition("p1", "extra_turn", True)
    assert new.get_player("p1").conditions == {"poisoned": 2, "extra_turn": True}


def test_without_condition_removes_key_immutably() -> None:
    state = GameState(
        room_code="AAAA", players=[Player(id="p1", name="A", conditions={"skip_next": True, "poisoned": 2})]
    )
    new = state.without_condition("p1", "skip_next")
    assert new.get_player("p1").conditions == {"poisoned": 2}
    # Source untouched.
    assert state.get_player("p1").conditions == {"skip_next": True, "poisoned": 2}


def test_without_condition_absent_key_is_noop() -> None:
    state = GameState(room_code="AAAA", players=[Player(id="p1", name="A")])
    new = state.without_condition("p1", "skip_next")
    assert new.get_player("p1").conditions == {}
    assert new is not state


def test_arbitrary_condition_round_trips_through_model_dump() -> None:
    """An open-ended condition key (not one the engine special-cases) must be
    visible in the WS snapshot, since Player.conditions is a serialized field."""
    state = GameState(room_code="AAAA", players=[Player(id="p1", name="A")])
    state = state.with_condition("p1", "poisoned", 2)
    dumped = state.model_dump()
    assert dumped["players"][0]["conditions"] == {"poisoned": 2}


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
