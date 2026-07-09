"""Tests for eval scorers (dsl_validity only; LLM scorers need an API key)."""

from __future__ import annotations

from tbwc.evals.eval_core import EvalItem, ScorerContext
from tbwc.evals.scorers import ALL_SCORERS, dsl_validity, intent_match_judge, target_accuracy, timing_accuracy


def _ctx(output, expected=None) -> ScorerContext:
    item = EvalItem(id="t1", input={"title": "x", "description": "y"}, expected=expected or {})
    return ScorerContext(item=item, output=output)


def test_all_scorers_count() -> None:
    assert len(ALL_SCORERS) == 4
    assert intent_match_judge in ALL_SCORERS
    assert target_accuracy in ALL_SCORERS
    assert timing_accuracy in ALL_SCORERS


def test_dsl_validity_valid_effect_program() -> None:
    # Use a real EffectProgram-shaped dict (matches tbwc.models.effects). Confirm the op shape
    # against effects.py: AddPointsOp = {"op": "add_points", "target": "self", "amount": 5}.
    ep = {"ops": [{"op": "add_points", "target": "self", "amount": 5}]}
    score = dsl_validity.evaluate(_ctx({"effect_program": ep}))
    assert score.score == 1.0


def test_dsl_validity_empty_program() -> None:
    score = dsl_validity.evaluate(_ctx({"effect_program": {"ops": []}}))
    assert score.score == 0.0


def test_dsl_validity_missing_output() -> None:
    assert dsl_validity.evaluate(_ctx({})).score == 0.0


def test_dsl_validity_malformed() -> None:
    score = dsl_validity.evaluate(_ctx({"effect_program": {"ops": [{"op": "not_a_real_op"}]}}))
    assert score.score == 0.0
