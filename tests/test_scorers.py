"""Tests for eval scorers (dsl_validity only; LLM scorers need an API key)."""

from __future__ import annotations

from evals.eval_core import EvalItem, ScorerContext
from evals.scorers import ALL_SCORERS, dsl_validity, intent_match_judge, target_accuracy, timing_accuracy


def _ctx(output, expected=None) -> ScorerContext:
    item = EvalItem(id="t1", input={"title": "x", "description": "y"}, expected=expected or {})
    return ScorerContext(item=item, output=output)


def test_all_scorers_count() -> None:
    assert len(ALL_SCORERS) == 4
    assert intent_match_judge in ALL_SCORERS
    assert target_accuracy in ALL_SCORERS
    assert timing_accuracy in ALL_SCORERS


def test_dsl_validity_valid_effect_program() -> None:
    # Use a real EffectProgram-shaped dict (matches models.effects). Confirm the op shape
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


def test_dsl_validity_accepts_mixed_resolution_plan() -> None:
    plan = {
        "steps": [
            {"kind": "ops", "ops": [{"op": "draw_cards", "target": "self", "amount": 2}]},
            {
                "kind": "snippet",
                "code": "def apply(state, ctx):\n    state.add_points('self', state.hand_size(state.actor_id))\n",
            },
        ]
    }

    assert dsl_validity.evaluate(_ctx({"resolution_plan": plan})).score == 1.0


def test_dsl_validity_rejects_invalid_plan_snippet() -> None:
    plan = {"steps": [{"kind": "snippet", "code": "def apply(state, ctx):\n    state.draw('self', 2)\n"}]}

    score = dsl_validity.evaluate(_ctx({"resolution_plan": plan}))

    assert score.score == 0.0
    assert "draw_cards" in score.metadata["reason"]
