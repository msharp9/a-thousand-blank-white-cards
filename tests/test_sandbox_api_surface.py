"""Tests for the SandboxGame façade."""

from __future__ import annotations

import copy
import inspect
from typing import get_args

import pytest

from engine.sandbox.api_surface import SandboxGame
from models.effects import Op

STATE = {
    "players": [
        {"id": "p1", "name": "Alice", "score": 10, "hand": ["c1"], "connected": True},
        {"id": "p2", "name": "Bob", "score": 5, "hand": [], "connected": True},
    ],
    "turn_index": 0,
    "draw_count": 1,
    "turn_order": ["p1", "p2"],
}
CTX = {"actor_id": "p1"}


def make_game() -> SandboxGame:
    return SandboxGame(copy.deepcopy(STATE), dict(CTX))


def test_read_players() -> None:
    g = make_game()
    ps = g.players()
    assert len(ps) == 2
    assert ps[0].id == "p1"
    assert ps[0].hand_size == 1


def test_player_lookup_and_missing() -> None:
    g = make_game()
    assert g.player("p2").name == "Bob"
    with pytest.raises(KeyError):
        g.player("nope")


def test_current_and_actor() -> None:
    g = make_game()
    assert g.current_player_id == "p1"
    assert g.actor_id == "p1"


def test_turn_order_reads_explicit_list() -> None:
    g = make_game()
    assert g.turn_order == ["p1", "p2"]


def test_turn_order_falls_back_to_players_in_list_order() -> None:
    state = copy.deepcopy(STATE)
    del state["turn_order"]
    g = SandboxGame(state, dict(CTX))
    assert g.turn_order == ["p1", "p2"]


def test_add_points_records_op() -> None:
    g = make_game()
    g.add_points("p2", 3)
    assert g.ops() == [{"op": "add_points", "target": "p2", "amount": 3}]


def test_no_state_mutation() -> None:
    g = make_game()
    g.add_points("p1", 99)
    assert g._state["players"][0]["score"] == 10


def test_invalid_amount_raises() -> None:
    g = make_game()
    with pytest.raises(ValueError):
        g.add_points("p1", -5)
    with pytest.raises(ValueError):
        g.subtract_points("p1", -1)


def test_bool_amount_rejected() -> None:
    g = make_game()
    with pytest.raises(ValueError):
        g.add_points("p1", True)


def test_set_points_and_skip_and_draw_count() -> None:
    g = make_game()
    g.set_points("p1", 0)
    g.skip_turn("p2")
    g.change_draw_count(3)
    ops = g.ops()
    assert {"op": "set_points", "target": "p1", "amount": 0} in ops
    assert {"op": "skip_turn", "target": "p2"} in ops
    assert {"op": "change_draw_count", "amount": 3} in ops


def test_note_truncates() -> None:
    g = make_game()
    g.custom_note("x" * 600)
    assert len(g.ops()[0]["note"]) == 500


def test_compatibility_aliases_record_canonical_ops() -> None:
    g = make_game()
    g.skip("p2")
    g.set_draw_count(3)
    g.note("hello")
    g.shuffle_into_deck("Reverse")
    assert [op["op"] for op in g.ops()] == ["skip_turn", "change_draw_count", "custom_note", "create_card"]


def test_ops_returns_copy() -> None:
    g = make_game()
    g.add_points("p1", 1)
    snapshot = g.ops()
    snapshot.append({"op": "hacked"})
    assert len(g.ops()) == 1  # internal list unaffected


class TestWideFacade:
    def _game(self, state=None, ctx=None):
        base_state = state or {
            "players": [
                {"id": "p1", "name": "A", "score": 0, "hand": ["c1", "c2"], "conditions": {"poisoned": 1}},
                {"id": "p2", "name": "B", "score": 5, "hand": ["c3"], "conditions": {}},
            ],
            "turn_index": 0,
            "deck": ["d1", "d2", "d3"],
            "rules": {"draw": 2, "play": 1},
            "cards": {"c1": {"id": "c1", "title": "One", "attributes": {"color": "red"}}},
        }
        return SandboxGame(base_state, ctx or {"actor_id": "p1"})

    def test_reads(self):
        g = self._game()
        assert g.deck_size == 3
        assert g.my_hand() == ["c1", "c2"]
        assert g.hand_size("p2") == 1
        assert g.conditions("p1") == {"poisoned": 1}
        assert g.rules()["draw"] == 2
        assert g.card("c1")["attributes"] == {"color": "red"}
        assert g.card("missing") is None

    def test_mutators_record_full_op_parity(self):
        g = self._game()
        g.draw_cards("self", 2)
        g.destroy_card(card_target="attr:color=red")
        g.set_win_condition("empty_hand")
        g.end_game(winner="self")
        g.set_rule("draw", 0)
        g.set_condition("id:p2", "poisoned", 2)
        g.set_card_attribute("all_in_hand", "color", "blue")
        g.create_card("Draw 2", ops=[{"op": "draw_cards", "args": {"amount": 2}}], count=2)
        g.shuffle_into_deck("Reverse")
        g.register_hook("on_turn_start", code="def apply(state, ctx):\n    pass\n")
        g.unregister_hook("source-card")
        g.reject_play("wrong color")
        g.extra_turn("self")
        g.reverse_order()
        g.scramble_order()
        g.steal_points("id:p2", "self", 3)
        recorded = {op["op"] for op in g.ops()}
        assert recorded == {
            "draw_cards",
            "destroy_card",
            "set_win_condition",
            "end_game",
            "set_rule",
            "set_condition",
            "set_card_attribute",
            "create_card",
            "register_hook",
            "unregister_hook",
            "reject_play",
            "extra_turn",
            "reverse_order",
            "scramble_order",
            "steal_points",
        }


def test_canonical_mutators_match_op_names_and_parameters() -> None:
    op_models = get_args(get_args(Op)[0])
    expected = {
        model.model_fields["op"].default: tuple(name for name in model.model_fields if name != "op")
        for model in op_models
    }
    aliases = {"skip", "set_draw_count", "note", "shuffle_into_deck"}
    read_and_control = {
        "players",
        "player",
        "rules",
        "my_hand",
        "hand_size",
        "conditions",
        "card",
        "reject_play",
        "ops",
    }
    public_methods = {
        name
        for name, member in inspect.getmembers(SandboxGame, predicate=inspect.isfunction)
        if not name.startswith("_")
    }

    assert public_methods == set(expected) | aliases | read_and_control
    for name, fields in expected.items():
        signature = inspect.signature(getattr(SandboxGame, name))
        assert tuple(signature.parameters)[1:] == fields
