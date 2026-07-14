"""Tests for eval scorers. The LLM judge is stubbed; everything runs offline."""

from __future__ import annotations

import evals.scorers as scorers
from evals.eval_core import EvalItem, ScorerContext
from evals.judge import Verdict
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
    reset_run_caches,
    sandbox_behavior,
    target_accuracy,
)


def _ctx(output, expected=None) -> ScorerContext:
    item = EvalItem(id="t1", input={"title": "x", "description": "y"}, expected=expected or {})
    return ScorerContext(item=item, output=output)


def _ops_plan(*ops: dict) -> dict:
    return {"resolution_plan": {"steps": [{"kind": "ops", "ops": list(ops)}]}}


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


class TestJudgeScorers:
    """The four judge scorers share ONE LLM call per output and map distinct Verdict fields."""

    def _install_counting_judge(self, monkeypatch) -> list[int]:
        calls: list[int] = []

        class FakeJudge:
            def evaluate(self, **kwargs) -> Verdict:
                calls.append(1)
                return Verdict(
                    intent_match=0.9,
                    persistence_correct=0.8,
                    target_placement_correct=0.7,
                    trigger_event_correct=1.0,
                    magnitude_sign_correct=0.6,
                    overall=0.75,
                    reason="stubbed",
                )

        monkeypatch.setattr(scorers, "_judge", lambda: FakeJudge())
        reset_run_caches()
        return calls

    def test_one_llm_call_shared_across_all_four_scorers(self, monkeypatch) -> None:
        calls = self._install_counting_judge(monkeypatch)
        ctx = _ctx(_ops_plan({"op": "add_points", "target": "self", "amount": 5}))

        assert intent_match_judge.evaluate(ctx).score == 0.9
        assert target_accuracy.evaluate(ctx).score == 0.7
        assert persistence_accuracy.evaluate(ctx).score == 0.8
        assert magnitude_sign.evaluate(ctx).score == 0.6
        assert len(calls) == 1

    def test_reset_run_caches_forces_a_fresh_judgement(self, monkeypatch) -> None:
        calls = self._install_counting_judge(monkeypatch)
        ctx = _ctx(_ops_plan({"op": "add_points", "target": "self", "amount": 5}))

        intent_match_judge.evaluate(ctx)
        reset_run_caches()
        intent_match_judge.evaluate(ctx)
        assert len(calls) == 2

    def test_effectless_output_still_judged_via_verdict_and_comment(self, monkeypatch) -> None:
        calls = self._install_counting_judge(monkeypatch)
        score = intent_match_judge.evaluate(_ctx({"verdict": "invalid", "comment": "no idea"}))
        assert len(calls) == 1
        assert score.metadata["reason"] == "stubbed"


class TestDslValidity:
    def test_valid_ops_plan(self) -> None:
        assert dsl_validity.evaluate(_ctx(_ops_plan({"op": "add_points", "target": "self", "amount": 5}))).score == 1.0

    def test_empty_plan(self) -> None:
        assert dsl_validity.evaluate(_ctx({"resolution_plan": {"steps": []}})).score == 0.0

    def test_missing_output(self) -> None:
        score = dsl_validity.evaluate(_ctx({}))
        assert score.score == 0.0
        assert "no resolution_plan" in score.metadata["reason"]

    def test_malformed_op(self) -> None:
        assert dsl_validity.evaluate(_ctx(_ops_plan({"op": "not_a_real_op"}))).score == 0.0

    def test_accepts_mixed_resolution_plan(self) -> None:
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

    def test_rejects_invalid_plan_snippet(self) -> None:
        plan = {"steps": [{"kind": "snippet", "code": "def apply(state, ctx):\n    state.draw('self', 2)\n"}]}
        score = dsl_validity.evaluate(_ctx({"resolution_plan": plan}))
        assert score.score == 0.0
        assert "draw_cards" in score.metadata["reason"]


class TestExecutability:
    def test_valid_ops_plan_runs(self) -> None:
        output = {"verdict": "ok", **_ops_plan({"op": "add_points", "target": "self", "amount": 5})}
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
        output = {"verdict": "ok", **_ops_plan({"op": "add_points", "target": "self", "amount": 5})}
        assert did_something.evaluate(_ctx(output)).score == 1.0

    def test_invalid_verdict_scores_zero(self) -> None:
        assert did_something.evaluate(_ctx({"verdict": "invalid"})).score == 0.0

    def test_custom_note_only_is_a_noop(self) -> None:
        output = {"verdict": "ok", **_ops_plan({"op": "custom_note", "note": "nothing"})}
        score = did_something.evaluate(_ctx(output))
        assert score.score == 0.0
        assert "no-op" in score.metadata["reason"]

    def test_shares_one_dry_run_with_executability(self, monkeypatch) -> None:
        calls: list[int] = []
        real = scorers._resolution_plan_from_output

        def counting(output):
            calls.append(1)
            return real(output)

        monkeypatch.setattr(scorers, "_resolution_plan_from_output", counting)
        reset_run_caches()
        output = {"verdict": "ok", **_ops_plan({"op": "add_points", "target": "self", "amount": 5})}
        ctx = _ctx(output)
        executability.evaluate(ctx)
        did_something.evaluate(ctx)
        assert len(calls) == 1


class TestSandboxBehavior:
    def _ctx(self, output: dict, expected: dict) -> ScorerContext:
        item = EvalItem(id="sb", input={"title": "x", "description": "y"}, expected=expected)
        return ScorerContext(item=item, output=output)

    def test_skips_when_no_expected_sandbox(self) -> None:
        score = sandbox_behavior.evaluate(self._ctx(_ops_plan(), {}))
        assert score.score == 1.0
        assert "skipped" in score.metadata

    def test_zero_when_no_generated_effect(self) -> None:
        expected = {"sandbox": "def apply(state, ctx):\n    state.add_points('self', 5)"}
        score = sandbox_behavior.evaluate(self._ctx({}, expected))
        assert score.score == 0.0

    def test_matching_ops_plan_scores_one(self) -> None:
        expected = {"sandbox": "def apply(state, ctx):\n    state.add_points('self', 5)"}
        output = _ops_plan({"op": "add_points", "target": "self", "amount": 5})
        assert sandbox_behavior.evaluate(self._ctx(output, expected)).score == 1.0

    def test_wrong_amount_scores_below_one(self) -> None:
        expected = {"sandbox": "def apply(state, ctx):\n    state.add_points('self', 5)"}
        output = _ops_plan({"op": "add_points", "target": "self", "amount": 500})
        assert sandbox_behavior.evaluate(self._ctx(output, expected)).score < 1.0

    def test_equivalent_snippet_scores_one(self) -> None:
        expected = {"sandbox": "def apply(state, ctx):\n    state.subtract_points('all_others', 2)"}
        output = {
            "resolution_plan": {
                "steps": [
                    {"kind": "snippet", "code": "def apply(state, ctx):\n    state.subtract_points('all_others', 2)"}
                ]
            }
        }
        assert sandbox_behavior.evaluate(self._ctx(output, expected)).score == 1.0

    def test_chooser_alias_normalises_to_chosen_player(self) -> None:
        # Expected sandbox addresses the chosen player via ctx; a generated ops
        # plan may say "chooser" — normalisation must treat them as equal.
        expected = {
            "sandbox": (
                "def apply(state, ctx):\n"
                '    chosen = "id:" + (ctx.get("chosen_player_id") or "")\n'
                "    state.add_points(chosen, 3)"
            )
        }
        output = _ops_plan({"op": "add_points", "target": "chooser", "amount": 3})
        assert sandbox_behavior.evaluate(self._ctx(output, expected)).score == 1.0
