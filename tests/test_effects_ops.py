"""Tests for the effects Op discriminated union and EffectProgram."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from models.effects import (
    AddPointsOp,
    DestroyCardOp,
    EffectProgram,
    StealPointsOp,
    is_known_target,
    map_authoring_target,
)


def test_discriminates_add_points() -> None:
    prog = EffectProgram.model_validate({"ops": [{"op": "add_points", "amount": 5}]})
    assert len(prog.ops) == 1
    assert isinstance(prog.ops[0], AddPointsOp)
    assert prog.ops[0].amount == 5
    assert prog.ops[0].target == "self"


def test_discriminates_steal_points() -> None:
    prog = EffectProgram.model_validate({"ops": [{"op": "steal_points", "from_target": "left_neighbor", "amount": 3}]})
    assert isinstance(prog.ops[0], StealPointsOp)
    assert prog.ops[0].from_target == "left_neighbor"
    assert prog.ops[0].to_target == "self"


def test_invalid_op_raises() -> None:
    with pytest.raises(ValidationError):
        EffectProgram.model_validate({"ops": [{"op": "not_a_real_op", "amount": 1}]})


def test_empty_program_defaults() -> None:
    prog = EffectProgram()
    assert prog.ops == []
    assert prog.requires_choice is False


# ---------------------------------------------------------------------------
# DestroyCardOp + CardTarget
# ---------------------------------------------------------------------------


def test_destroy_card_op_back_compat_card_id() -> None:
    """Legacy shape: a bare card_id with no card_target still validates."""
    op = DestroyCardOp(card_id="c1")
    assert op.card_id == "c1"
    assert op.card_target is None


def test_destroy_card_op_accepts_card_target() -> None:
    op = DestroyCardOp(card_target="all_in_play")
    assert op.card_target == "all_in_play"
    assert op.card_id is None


def test_destroy_card_op_rejects_bad_card_target() -> None:
    with pytest.raises(ValidationError):
        DestroyCardOp(card_target="not_a_card_target")


def test_destroy_card_op_defaults_both_none() -> None:
    op = DestroyCardOp()
    assert op.card_id is None
    assert op.card_target is None


def test_destroy_card_op_discriminates_from_program() -> None:
    prog = EffectProgram.model_validate({"ops": [{"op": "destroy_card", "card_target": "chosen_card"}]})
    assert isinstance(prog.ops[0], DestroyCardOp)
    assert prog.ops[0].card_target == "chosen_card"


# ---------------------------------------------------------------------------
# map_authoring_target
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        # authoring vocabulary
        ("self", "self"),
        ("player", "chooser"),
        ("all", "all"),
        # defensive synonyms
        ("opponent", "chooser"),
        ("all_players", "all"),
        ("everyone", "all"),
        ("others", "all_others"),
        # gold-corpus vocabulary drift (bead ao7): a choice-requiring target and
        # the turn-order successor now have real aliases instead of silently
        # defaulting to "self".
        ("chosen_player", "chooser"),
        ("next_player", "right_neighbor"),
        # case / whitespace tolerance
        ("Player", "chooser"),
        ("  ALL  ", "all"),
    ],
)
def test_map_authoring_target_aliases(raw: str, expected: str) -> None:
    assert map_authoring_target(raw) == expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("self", True),
        ("chosen_player", True),
        ("next_player", True),
        ("player", True),
        ("banana", False),
        ("center", False),
    ],
)
def test_is_known_target(raw: str, expected: bool) -> None:
    assert is_known_target(raw) is expected


@pytest.mark.parametrize(
    "runtime",
    [
        "self",
        "left_neighbor",
        "right_neighbor",
        "all",
        "all_others",
        "chooser",
        "target_player",
        "player_with_most_points",
        "player_with_least_points",
        "player_with_empty_hand",
    ],
)
def test_map_authoring_target_passthrough(runtime: str) -> None:
    """Already-valid runtime Targets pass through unchanged."""
    assert map_authoring_target(runtime) == runtime


def test_map_authoring_target_center_is_not_a_target() -> None:
    """'center' is a placement, not a player target — it must not map."""
    with pytest.raises(ValueError, match="center"):
        map_authoring_target("center")


def test_map_authoring_target_unknown_raises() -> None:
    with pytest.raises(ValueError, match="Cannot map authoring target"):
        map_authoring_target("banana")


def test_map_authoring_target_unknown_default() -> None:
    """A documented safe default is returned instead of raising when provided."""
    assert map_authoring_target("banana", default="chooser") == "chooser"
    # center also falls back to the default rather than raising
    assert map_authoring_target("center", default="chooser") == "chooser"
