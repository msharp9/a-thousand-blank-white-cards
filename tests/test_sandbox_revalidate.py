"""Tests for sandbox.revalidate parse_diff + apply_snippet_diff."""

from __future__ import annotations

import pytest

from engine.events import GameEvent, HookContext
from models.effects import AddPointsOp
from models.game_state import GameState, Player
from engine.sandbox.revalidate import DiffValidationError, apply_snippet_diff, parse_diff


def test_parse_valid_ops() -> None:
    program = parse_diff([{"op": "add_points", "target": "self", "amount": 5}])
    assert len(program.ops) == 1
    assert isinstance(program.ops[0], AddPointsOp)
    assert program.ops[0].amount == 5


def test_parse_unknown_op_raises() -> None:
    with pytest.raises(DiffValidationError):
        parse_diff([{"op": "hack_the_planet", "target": "p1"}])


def test_parse_bad_type_raises() -> None:
    with pytest.raises(DiffValidationError):
        parse_diff("not a list")  # type: ignore[arg-type]


def test_parse_too_many_ops_raises() -> None:
    with pytest.raises(DiffValidationError, match="too large"):
        parse_diff([{"op": "custom_note", "note": "x"}] * 51)


def test_parse_non_dict_element_raises() -> None:
    with pytest.raises(DiffValidationError):
        parse_diff(["not a dict"])  # type: ignore[list-item]


def test_parse_empty_ops() -> None:
    assert parse_diff([]).ops == []


def test_apply_snippet_diff_mutates_via_engine() -> None:
    state = GameState(room_code="AAAA", players=[Player(id="p1", name="A", score=10)])
    ctx = HookContext(event=GameEvent.ON_PLAY, actor_id="p1")
    new = apply_snippet_diff(state, [{"op": "add_points", "target": "self", "amount": 5}], ctx)
    assert new.get_player("p1").score == 15
    assert state.get_player("p1").score == 10  # original unchanged
