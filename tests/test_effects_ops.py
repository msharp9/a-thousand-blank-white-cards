"""Tests for the effects Op discriminated union and EffectProgram."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from tbwc.models.effects import AddPointsOp, EffectProgram, StealPointsOp


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
