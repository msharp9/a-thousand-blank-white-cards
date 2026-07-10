"""Tests for tbwc.engine.compile — card authoring ops -> runtime EffectProgram."""

from __future__ import annotations

from tbwc.engine.compile import compile_card
from tbwc.models.effects import (
    AddPointsOp,
    ChangeDrawCountOp,
    CustomNoteOp,
    DestroyCardOp,
    DrawCardsOp,
    EffectProgram,
    ExtraTurnOp,
    ReverseOrderOp,
    SetPointsOp,
    SetWinConditionOp,
    SkipTurnOp,
    StealPointsOp,
    SubtractPointsOp,
)


def _card(ops: list[dict]) -> dict:
    """Build a minimal card dict carrying top-level authoring ops."""
    return {"id": "c1", "title": "T", "description": "D", "ops": ops}


# ---------------------------------------------------------------------------
# Supported ops compile to the right runtime Op with the right fields
# ---------------------------------------------------------------------------


def test_add_points() -> None:
    prog = compile_card(_card([{"op": "add_points", "args": {"amount": 5, "target": "self"}}]))
    assert isinstance(prog, EffectProgram)
    assert len(prog.ops) == 1
    op = prog.ops[0]
    assert isinstance(op, AddPointsOp)
    assert op.amount == 5
    assert op.target == "self"
    assert prog.requires_choice is False


def test_add_points_default_target() -> None:
    prog = compile_card(_card([{"op": "add_points", "args": {"amount": 2}}]))
    assert isinstance(prog.ops[0], AddPointsOp)
    assert prog.ops[0].target == "self"


def test_subtract_points() -> None:
    prog = compile_card(_card([{"op": "subtract_points", "args": {"amount": 3, "target": "all"}}]))
    op = prog.ops[0]
    assert isinstance(op, SubtractPointsOp)
    assert op.amount == 3
    assert op.target == "all"


def test_set_points() -> None:
    prog = compile_card(_card([{"op": "set_points", "args": {"amount": 0, "target": "all_others"}}]))
    op = prog.ops[0]
    assert isinstance(op, SetPointsOp)
    assert op.amount == 0
    assert op.target == "all_others"


def test_skip_turn() -> None:
    prog = compile_card(_card([{"op": "skip_turn", "args": {"target": "self"}}]))
    assert isinstance(prog.ops[0], SkipTurnOp)
    assert prog.ops[0].target == "self"


def test_extra_turn_default_target() -> None:
    prog = compile_card(_card([{"op": "extra_turn", "args": {}}]))
    assert isinstance(prog.ops[0], ExtraTurnOp)
    assert prog.ops[0].target == "self"


def test_reverse_order() -> None:
    prog = compile_card(_card([{"op": "reverse_order", "args": {}}]))
    assert isinstance(prog.ops[0], ReverseOrderOp)


def test_change_draw_count() -> None:
    prog = compile_card(_card([{"op": "change_draw_count", "args": {"amount": 3}}]))
    assert isinstance(prog.ops[0], ChangeDrawCountOp)
    assert prog.ops[0].amount == 3


def test_draw_cards_default_amount() -> None:
    prog = compile_card(_card([{"op": "draw_cards", "args": {"target": "self"}}]))
    op = prog.ops[0]
    assert isinstance(op, DrawCardsOp)
    assert op.amount == 1
    assert op.target == "self"


def test_draw_cards_explicit_amount() -> None:
    prog = compile_card(_card([{"op": "draw_cards", "args": {"amount": 2}}]))
    assert prog.ops[0].amount == 2


def test_set_win_condition() -> None:
    prog = compile_card(_card([{"op": "set_win_condition", "args": {"kind": "first_to", "threshold": 50}}]))
    op = prog.ops[0]
    assert isinstance(op, SetWinConditionOp)
    assert op.kind == "first_to"
    assert op.threshold == 50


def test_set_win_condition_no_threshold() -> None:
    prog = compile_card(_card([{"op": "set_win_condition", "args": {"kind": "highest_points"}}]))
    op = prog.ops[0]
    assert isinstance(op, SetWinConditionOp)
    assert op.threshold is None


def test_custom_note_from_note() -> None:
    prog = compile_card(_card([{"op": "custom_note", "args": {"note": "hello"}}]))
    assert isinstance(prog.ops[0], CustomNoteOp)
    assert prog.ops[0].note == "hello"


def test_custom_note_from_text_fallback() -> None:
    prog = compile_card(_card([{"op": "custom_note", "args": {"text": "flavour"}}]))
    assert prog.ops[0].note == "flavour"


def test_destroy_card_by_target() -> None:
    prog = compile_card(_card([{"op": "destroy_card", "args": {"card_target": "all_in_play"}}]))
    op = prog.ops[0]
    assert isinstance(op, DestroyCardOp)
    assert op.card_target == "all_in_play"
    assert op.card_id is None


def test_destroy_card_by_id() -> None:
    prog = compile_card(_card([{"op": "destroy_card", "args": {"card_id": "x1"}}]))
    op = prog.ops[0]
    assert isinstance(op, DestroyCardOp)
    assert op.card_id == "x1"
    assert op.card_target is None


# ---------------------------------------------------------------------------
# steal_points: from/to mapping
# ---------------------------------------------------------------------------


def test_steal_points_from_to() -> None:
    prog = compile_card(_card([{"op": "steal_points", "args": {"from": "left_neighbor", "to": "self", "amount": 4}}]))
    op = prog.ops[0]
    assert isinstance(op, StealPointsOp)
    assert op.from_target == "left_neighbor"
    assert op.to_target == "self"
    assert op.amount == 4


def test_steal_points_from_target_aliases() -> None:
    prog = compile_card(
        _card([{"op": "steal_points", "args": {"from_target": "right_neighbor", "to_target": "self", "amount": 1}}])
    )
    op = prog.ops[0]
    assert op.from_target == "right_neighbor"
    assert op.to_target == "self"


def test_steal_points_default_to_self() -> None:
    prog = compile_card(_card([{"op": "steal_points", "args": {"from": "left_neighbor", "amount": 2}}]))
    assert prog.ops[0].to_target == "self"


# ---------------------------------------------------------------------------
# requires_choice: chooser / target_player / chosen_card
# ---------------------------------------------------------------------------


def test_requires_choice_for_player_authoring_target() -> None:
    # authoring "player" maps to runtime "chooser"
    prog = compile_card(_card([{"op": "add_points", "args": {"amount": 5, "target": "player"}}]))
    assert prog.ops[0].target == "chooser"
    assert prog.requires_choice is True


def test_requires_choice_for_target_player() -> None:
    prog = compile_card(_card([{"op": "skip_turn", "args": {"target": "target_player"}}]))
    assert prog.requires_choice is True


def test_requires_choice_for_steal_from_chooser() -> None:
    prog = compile_card(_card([{"op": "steal_points", "args": {"from": "player", "amount": 3}}]))
    assert prog.ops[0].from_target == "chooser"
    assert prog.requires_choice is True


def test_requires_choice_for_chosen_card() -> None:
    prog = compile_card(_card([{"op": "destroy_card", "args": {"card_target": "chosen_card"}}]))
    assert prog.requires_choice is True


def test_no_requires_choice_for_all_in_play() -> None:
    prog = compile_card(_card([{"op": "destroy_card", "args": {"card_target": "all_in_play"}}]))
    assert prog.requires_choice is False


# ---------------------------------------------------------------------------
# Unknown ops are skipped; malformed args skipped; empty -> None
# ---------------------------------------------------------------------------


def test_unknown_op_is_skipped_but_keeps_others() -> None:
    prog = compile_card(
        _card(
            [
                {"op": "multiply_points", "args": {"amount": 2}},
                {"op": "add_points", "args": {"amount": 5}},
            ]
        )
    )
    assert len(prog.ops) == 1
    assert isinstance(prog.ops[0], AddPointsOp)


def test_all_unknown_ops_returns_none() -> None:
    prog = compile_card(
        _card(
            [
                {"op": "multiply_points", "args": {"amount": 2}},
                {"op": "trade_hands", "args": {}},
                {"op": "no_op", "args": {}},
                {"op": "discard_hand", "args": {}},
            ]
        )
    )
    assert prog is None


def test_malformed_missing_amount_is_skipped() -> None:
    prog = compile_card(_card([{"op": "add_points", "args": {"target": "self"}}]))
    assert prog is None


def test_malformed_op_skipped_others_kept() -> None:
    prog = compile_card(
        _card(
            [
                {"op": "add_points", "args": {"target": "self"}},  # missing amount -> skip
                {"op": "reverse_order", "args": {}},
            ]
        )
    )
    assert len(prog.ops) == 1
    assert isinstance(prog.ops[0], ReverseOrderOp)


def test_steal_points_missing_from_is_skipped() -> None:
    prog = compile_card(_card([{"op": "steal_points", "args": {"amount": 3}}]))
    assert prog is None


def test_set_win_condition_missing_kind_is_skipped() -> None:
    prog = compile_card(_card([{"op": "set_win_condition", "args": {"threshold": 10}}]))
    assert prog is None


def test_op_entry_missing_name_is_skipped() -> None:
    prog = compile_card(_card([{"args": {"amount": 5}}]))
    assert prog is None


def test_non_dict_op_entry_is_skipped() -> None:
    prog = compile_card(_card(["not a dict", {"op": "add_points", "args": {"amount": 1}}]))
    assert len(prog.ops) == 1


def test_non_dict_args_is_skipped() -> None:
    prog = compile_card(_card([{"op": "add_points", "args": "nope"}]))
    assert prog is None


# ---------------------------------------------------------------------------
# No structured ops -> None
# ---------------------------------------------------------------------------


def test_no_ops_key_returns_none() -> None:
    assert compile_card({"id": "c1", "title": "T", "description": "blank"}) is None


def test_empty_ops_list_returns_none() -> None:
    assert compile_card(_card([])) is None


def test_reads_from_canonical_when_no_top_level_ops() -> None:
    card = {
        "id": "c1",
        "title": "T",
        "description": "D",
        "canonical": {"ops": [{"op": "add_points", "args": {"amount": 7}}]},
    }
    prog = compile_card(card)
    assert isinstance(prog, EffectProgram)
    assert prog.ops[0].amount == 7


def test_canonical_without_ops_returns_none() -> None:
    card = {"id": "c1", "title": "T", "description": "D", "canonical": {"timing": "immediate"}}
    assert compile_card(card) is None


# ---------------------------------------------------------------------------
# Multi-op program preserves order
# ---------------------------------------------------------------------------


def test_multi_op_program_preserves_order() -> None:
    prog = compile_card(
        _card(
            [
                {"op": "add_points", "args": {"amount": 5}},
                {"op": "draw_cards", "args": {"amount": 2}},
                {"op": "reverse_order", "args": {}},
            ]
        )
    )
    assert [type(op) for op in prog.ops] == [AddPointsOp, DrawCardsOp, ReverseOrderOp]


def test_amount_coerced_to_int() -> None:
    prog = compile_card(_card([{"op": "add_points", "args": {"amount": "8"}}]))
    assert prog.ops[0].amount == 8
