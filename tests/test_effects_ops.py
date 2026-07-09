"""Tests for the effects Op discriminated union and EffectProgram."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from tbwc.models.effects import AddPointsOp, EffectProgram, StealPointsOp, map_authoring_target


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
        # case / whitespace tolerance
        ("Player", "chooser"),
        ("  ALL  ", "all"),
    ],
)
def test_map_authoring_target_aliases(raw: str, expected: str) -> None:
    assert map_authoring_target(raw) == expected


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
