"""Tests for the SandboxGame façade."""

from __future__ import annotations

import copy

import pytest

from engine.sandbox.api_surface import SandboxGame

STATE = {
    "players": [
        {"id": "p1", "name": "Alice", "score": 10, "hand": ["c1"], "connected": True},
        {"id": "p2", "name": "Bob", "score": 5, "hand": [], "connected": True},
    ],
    "turn_index": 0,
    "draw_count": 1,
    "direction": 1,
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
    g.skip("p2")
    g.set_draw_count(3)
    ops = g.ops()
    assert {"op": "set_points", "target": "p1", "amount": 0} in ops
    assert {"op": "skip_turn", "target": "p2"} in ops
    assert {"op": "change_draw_count", "amount": 3} in ops


def test_note_truncates() -> None:
    g = make_game()
    g.note("x" * 600)
    assert len(g.ops()[0]["note"]) == 500


def test_ops_returns_copy() -> None:
    g = make_game()
    g.add_points("p1", 1)
    snapshot = g.ops()
    snapshot.append({"op": "hacked"})
    assert len(g.ops()) == 1  # internal list unaffected
