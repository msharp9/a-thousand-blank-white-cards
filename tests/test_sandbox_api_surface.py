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


def test_legacy_destroy_card_positional_target_is_preserved() -> None:
    g = make_game()

    g.destroy_card("this")

    assert g.ops() == [{"op": "destroy_card", "card_target": "this"}]


def test_create_card_routes_to_target_player() -> None:
    g = make_game()

    g.create_card("Double Cat", destination="hand", target="id:p2")

    op = g.ops()[0]
    assert op["destination"] == "hand"
    assert op["target"] == "id:p2"


def test_create_card_coerces_player_target_passed_as_destination() -> None:
    # The exact mistake the agent made: a player Target given as `destination`.
    g = make_game()

    g.create_card("Double Cat", destination="id:p2")

    op = g.ops()[0]
    assert op["destination"] == "hand"
    assert op["target"] == "id:p2"


def test_create_card_defaults_target_to_self() -> None:
    g = make_game()

    g.create_card("Gift", destination="hand")

    assert g.ops()[0]["target"] == "self"


def test_register_hook_rejects_scope_without_code() -> None:
    g = make_game()

    with pytest.raises(ValueError, match="requires sandbox code"):
        g.register_hook("on_validate_play", "player")


def test_history_reads_are_bounded_filtered_and_aggregated() -> None:
    state = copy.deepcopy(STATE)
    state["history_events"] = [
        {
            "sequence": 1,
            "kind": "draw",
            "actor_id": "p1",
            "target_player_ids": ["p2"],
            "amount": 2,
        },
        {
            "sequence": 2,
            "kind": "play",
            "actor_id": "p1",
            "target_player_ids": ["p1"],
            "card_id": "public-card",
        },
    ]
    game = SandboxGame(state, dict(CTX))

    assert game.history(kind="draw", player_id="p2", limit=500) == [state["history_events"][0]]
    assert game.draw_totals() == {"p1": 0, "p2": 2}


def test_end_game_records_multiple_explicit_winner_targets() -> None:
    game = make_game()

    game.end_game(["id:p1", "id:p2"])

    assert game.ops() == [{"op": "end_game", "winners": ["id:p1", "id:p2"]}]


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
        assert g.conditions("p1").get("PoIsOnEd") == 1
        assert g.conditions("p1")["POISONED"] == 1
        assert "Poisoned" in g.conditions("p1")
        assert g.rules()["draw"] == 2
        assert g.card("c1")["attributes"] == {"color": "red"}
        assert g.card("missing") is None

    def test_mutators_record_full_op_parity(self):
        g = self._game()
        g.draw_cards("self", 2)
        g.destroy_card(card_target="attr:color=red")
        g.transfer_card("this", "id:p2")
        g.set_win_condition("empty_hand")
        g.end_game(winner="self")
        g.set_rule("draw", 0)
        g.set_condition("id:p2", "poisoned", 2)
        g.set_card_attribute("all_in_hand", "color", "blue")
        g.create_card("Draw 2", ops=[{"op": "draw_cards", "args": {"amount": 2}}], count=2)
        g.shuffle_into_deck("Reverse")
        g.move_cards(from_zone="deck", selector="top", count=2, to_zone="discard")
        g.shuffle_deck(include_discard=True)
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
            "transfer_card",
            "set_win_condition",
            "end_game",
            "set_rule",
            "set_condition",
            "set_card_attribute",
            "create_card",
            "move_cards",
            "shuffle_deck",
            "register_hook",
            "unregister_hook",
            "reject_play",
            "extra_turn",
            "reverse_order",
            "scramble_order",
            "steal_points",
        }

    def test_register_hook_records_player_facing_metadata(self):
        g = self._game()
        g.register_hook(
            "on_turn_end",
            code="def apply(state, ctx):\n    pass\n",
            title="Cursed players discard a card at the end of their turn.",
            condition_keys=["Cursed", " cursed "],
        )
        hook = g.ops()[0]
        assert hook["title"] == "Cursed players discard a card at the end of their turn."
        assert hook["condition_keys"] == ["cursed"]


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
        "history",
        "draw_totals",
        "reject_play",
        "ops",
    }
    public_methods = {
        name
        for name, member in inspect.getmembers(SandboxGame, predicate=inspect.isfunction)
        if not name.startswith("_")
    }

    # Op fields deliberately absent from the snippet-facing signature:
    # roll_die.result is engine-filled (a snippet supplying it could forge rolls).
    trusted_only_fields = {"roll_die": ("result",)}

    assert public_methods == set(expected) | aliases | read_and_control
    for name, fields in expected.items():
        hidden = trusted_only_fields.get(name, ())
        signature = inspect.signature(getattr(SandboxGame, name))
        assert tuple(signature.parameters)[1:] == tuple(f for f in fields if f not in hidden)


class TestCounterPlay:
    def _game(self):
        state = {
            "players": [{"id": "p1", "name": "A", "score": 0, "hand": [], "connected": True}],
            "rules": {"draw": 1},
            "cards": {},
        }
        return SandboxGame(state, {"actor_id": "p1"})

    def test_counter_play_records_op(self):
        g = self._game()
        g.counter_play("steal_hand")
        assert g.ops() == [{"op": "counter_play", "mode": "steal_hand"}]

    def test_counter_play_defaults_negate(self):
        g = self._game()
        g.counter_play()
        assert g.ops() == [{"op": "counter_play", "mode": "negate"}]

    def test_counter_play_rejects_unknown_mode(self):
        g = self._game()
        with pytest.raises(ValueError):
            g.counter_play("obliterate")


class TestRollDie:
    def test_roll_records_pre_resolved_op(self):
        g = make_game()
        total = g.roll_die(sides=6, count=2, outcome="add_points")
        (op,) = g.ops()
        assert op["op"] == "roll_die"
        assert (op["sides"], op["count"], op["target"], op["outcome"]) == (6, 2, "self", "add_points")
        assert len(op["result"]) == 2
        assert all(1 <= v <= 6 for v in op["result"])
        assert total == sum(op["result"])

    def test_result_kwarg_is_not_snippet_callable(self):
        g = make_game()
        with pytest.raises(TypeError):
            g.roll_die(sides=6, count=2, result=[3, 5])
        assert g.ops() == []

    def test_seeded_rng_replays_identically(self):
        first = SandboxGame({"players": [{"id": "p1", "name": "A", "score": 0, "hand": []}]}, {}, rng_seed=0)
        second = SandboxGame({"players": [{"id": "p1", "name": "A", "score": 0, "hand": []}]}, {}, rng_seed=0)
        assert first.roll_die(sides=1000, count=5) == second.roll_die(sides=1000, count=5)
        assert first.ops() == second.ops()

    def test_rejects_bad_arguments(self):
        g = make_game()
        with pytest.raises(ValueError):
            g.roll_die(sides=1)
        with pytest.raises(ValueError):
            g.roll_die(count=0)
        with pytest.raises(ValueError):
            g.roll_die(outcome="explode")
        assert g.ops() == []


class TestDiscardRandom:
    def test_records_unresolved_op(self):
        g = make_game()
        assert g.discard_random("all_others", 2) is None
        assert g.ops() == [{"op": "discard_random", "target": "all_others", "count": 2}]

    def test_defaults(self):
        g = make_game()
        g.discard_random()
        assert g.ops() == [{"op": "discard_random", "target": "self", "count": 1}]

    def test_rejects_bad_count(self):
        g = make_game()
        with pytest.raises(ValueError):
            g.discard_random(count=0)
        with pytest.raises(ValueError):
            g.discard_random(count=11)
        assert g.ops() == []
