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


def test_parse_rejects_chooser_target() -> None:
    with pytest.raises(DiffValidationError, match="choice-requiring"):
        parse_diff([{"op": "add_points", "target": "chooser", "amount": 5}])


def test_parse_rejects_target_player() -> None:
    with pytest.raises(DiffValidationError, match="choice-requiring"):
        parse_diff([{"op": "skip_turn", "target": "target_player"}])


def test_parse_rejects_chosen_card() -> None:
    with pytest.raises(DiffValidationError, match="choice-requiring"):
        parse_diff([{"op": "destroy_card", "card_target": "chosen_card"}])


def test_parse_rejects_end_game_chooser_winner() -> None:
    with pytest.raises(DiffValidationError, match="choice-requiring"):
        parse_diff([{"op": "end_game", "winner": "chooser"}])


def test_parse_allows_end_game_self_winner() -> None:
    program = parse_diff([{"op": "end_game", "winner": "self"}])
    assert program.ops[0].winner == "self"


def test_hook_origin_rejects_register_hook() -> None:
    with pytest.raises(DiffValidationError, match="self-replicating"):
        parse_diff(
            [{"op": "register_hook", "event": "on_play", "code": "def apply(state, ctx):\n    pass\n"}],
            origin="hook",
        )


def test_play_origin_allows_register_hook() -> None:
    program = parse_diff(
        [{"op": "register_hook", "event": "on_play", "code": "def apply(state, ctx):\n    pass\n"}],
        origin="play",
    )
    assert program.ops[0].op == "register_hook"


def test_reject_play_never_parses() -> None:
    with pytest.raises(DiffValidationError, match="reject_play"):
        parse_diff([{"op": "reject_play", "reason": "nope"}])


def test_extract_veto_finds_reason() -> None:
    from engine.sandbox.revalidate import extract_veto

    assert (
        extract_veto([{"op": "custom_note", "note": "x"}, {"op": "reject_play", "reason": "wrong color"}])
        == "wrong color"
    )
    assert extract_veto([{"op": "custom_note", "note": "x"}]) is None


def test_counter_play_rejected_in_play_diff() -> None:
    with pytest.raises(DiffValidationError, match="reaction window"):
        parse_diff([{"op": "counter_play", "mode": "negate"}], origin="play")


def test_counter_play_rejected_in_hook_diff() -> None:
    with pytest.raises(DiffValidationError, match="reaction window"):
        parse_diff([{"op": "counter_play", "mode": "negate"}], origin="hook")


def test_extract_counter_splits_mode_and_side_ops() -> None:
    from engine.sandbox.revalidate import extract_counter

    raw = [
        {"op": "add_points", "target": "self", "amount": 2},
        {"op": "counter_play", "mode": "steal_hand"},
        {"op": "counter_play", "mode": "redirect"},  # first mode wins
    ]
    mode, rest = extract_counter(raw)
    assert mode == "steal_hand"
    assert rest == [{"op": "add_points", "target": "self", "amount": 2}]
    # Post-extraction, the remaining diff parses cleanly for the reaction origin.
    assert len(parse_diff(rest, origin="reaction").ops) == 1


def test_extract_counter_none_when_no_counter() -> None:
    from engine.sandbox.revalidate import extract_counter

    mode, rest = extract_counter([{"op": "custom_note", "note": "boo"}])
    assert mode is None
    assert len(rest) == 1
