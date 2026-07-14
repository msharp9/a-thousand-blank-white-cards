"""Tests for eval scorers (dsl_validity only; LLM scorers need an API key)."""

from __future__ import annotations

from evals.eval_core import EvalItem, ScorerContext
from evals.scorers import (
    ALL_SCORERS,
    DETERMINISTIC_SCORERS,
    JUDGE_SCORERS,
    did_something,
    dsl_validity,
    executability,
    intent_match_judge,
    magnitude_sign,
    persistence_accuracy,
    sandbox_behavior,
    target_accuracy,
)


def _ctx(output, expected=None) -> ScorerContext:
    item = EvalItem(id="t1", input={"title": "x", "description": "y"}, expected=expected or {})
    return ScorerContext(item=item, output=output)


def test_all_scorers_count() -> None:
    assert len(ALL_SCORERS) == 8
    assert set(ALL_SCORERS) == set(JUDGE_SCORERS) | set(DETERMINISTIC_SCORERS)
    for scorer in (
        sandbox_behavior,
        intent_match_judge,
        target_accuracy,
        persistence_accuracy,
        magnitude_sign,
        executability,
        did_something,
    ):
        assert scorer in ALL_SCORERS


class TestExecutability:
    def test_valid_ops_plan_runs(self) -> None:
        output = {
            "verdict": "ok",
            "resolution_plan": {
                "steps": [{"kind": "ops", "ops": [{"op": "add_points", "target": "self", "amount": 5}]}]
            },
        }
        assert executability.evaluate(_ctx(output)).score == 1.0

    def test_no_plan_scores_zero(self) -> None:
        assert executability.evaluate(_ctx({"verdict": "invalid"})).score == 0.0

    def test_invalid_snippet_scores_zero(self) -> None:
        output = {
            "verdict": "ok",
            "resolution_plan": {"steps": [{"kind": "snippet", "code": "def apply(state, ctx):\n    state.nope(5)\n"}]},
        }
        score = executability.evaluate(_ctx(output))
        assert score.score == 0.0
        assert "reason" in score.metadata


class TestDidSomething:
    def test_real_effect_scores_one(self) -> None:
        output = {
            "verdict": "ok",
            "resolution_plan": {
                "steps": [{"kind": "ops", "ops": [{"op": "add_points", "target": "self", "amount": 5}]}]
            },
        }
        assert did_something.evaluate(_ctx(output)).score == 1.0

    def test_invalid_verdict_scores_zero(self) -> None:
        assert did_something.evaluate(_ctx({"verdict": "invalid"})).score == 0.0

    def test_custom_note_only_is_a_noop(self) -> None:
        output = {
            "verdict": "ok",
            "resolution_plan": {"steps": [{"kind": "ops", "ops": [{"op": "custom_note", "note": "nothing"}]}]},
        }
        score = did_something.evaluate(_ctx(output))
        assert score.score == 0.0
        assert "no-op" in score.metadata["reason"]


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


class TestSandboxBehavior:
    def _ctx(self, output: dict, expected: dict):
        from evals.eval_core import EvalItem, ScorerContext

        item = EvalItem(id="sb", input={"title": "x", "description": "y"}, expected=expected)
        return ScorerContext(item=item, output=output)

    def test_skips_when_no_expected_sandbox(self) -> None:
        score = sandbox_behavior.evaluate(self._ctx({"effect_program": {"ops": []}}, {}))
        assert score.score == 1.0
        assert "skipped" in score.metadata

    def test_zero_when_no_generated_effect(self) -> None:
        expected = {"sandbox": "def apply(state, ctx):\n    state.add_points('self', 5)"}
        score = sandbox_behavior.evaluate(self._ctx({}, expected))
        assert score.score == 0.0

    def test_matching_ops_program_scores_one(self) -> None:
        expected = {"sandbox": "def apply(state, ctx):\n    state.add_points('self', 5)"}
        output = {"effect_program": {"ops": [{"op": "add_points", "target": "self", "amount": 5}]}}
        score = sandbox_behavior.evaluate(self._ctx(output, expected))
        assert score.score == 1.0

    def test_wrong_amount_scores_below_one(self) -> None:
        expected = {"sandbox": "def apply(state, ctx):\n    state.add_points('self', 5)"}
        output = {"effect_program": {"ops": [{"op": "add_points", "target": "self", "amount": 500}]}}
        score = sandbox_behavior.evaluate(self._ctx(output, expected))
        assert score.score < 1.0

    def test_equivalent_snippet_scores_one(self) -> None:
        expected = {"sandbox": "def apply(state, ctx):\n    state.subtract_points('all_others', 2)"}
        output = {
            "resolution_plan": {
                "steps": [
                    {"kind": "snippet", "code": "def apply(state, ctx):\n    state.subtract_points('all_others', 2)"}
                ]
            }
        }
        score = sandbox_behavior.evaluate(self._ctx(output, expected))
        assert score.score == 1.0

    def test_chooser_alias_normalises_to_chosen_player(self) -> None:
        # Expected sandbox addresses the chosen player via ctx; a generated ops
        # program may say "chooser" — normalisation must treat them as equal.
        expected = {
            "sandbox": (
                "def apply(state, ctx):\n"
                '    chosen = "id:" + (ctx.get("chosen_player_id") or "")\n'
                "    state.add_points(chosen, 3)"
            )
        }
        output = {"effect_program": {"ops": [{"op": "add_points", "target": "chooser", "amount": 3}]}}
        score = sandbox_behavior.evaluate(self._ctx(output, expected))
        assert score.score == 1.0
