"""Tests for eval scorers. The LLM judge is stubbed; everything runs offline."""

from __future__ import annotations

import json
import pathlib

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
    magnitude_value,
    placement_accuracy,
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
    assert len(ALL_SCORERS) == 10
    assert set(ALL_SCORERS) == set(JUDGE_SCORERS) | set(DETERMINISTIC_SCORERS)
    for scorer in (
        sandbox_behavior,
        intent_match_judge,
        target_accuracy,
        persistence_accuracy,
        magnitude_sign,
        magnitude_value,
        placement_accuracy,
        executability,
        did_something,
    ):
        assert scorer in ALL_SCORERS


class TestPlacementAccuracy:
    def test_exact_match_scores_one(self):
        score = placement_accuracy.evaluate(_ctx({"placement": "player"}, {"placement": "player"}))
        assert score.score == 1.0
        assert score.metadata["expected"] == "player"

    def test_mismatch_scores_zero(self):
        score = placement_accuracy.evaluate(_ctx({"placement": "discard"}, {"placement": "center"}))
        assert score.score == 0.0
        assert score.metadata["reason"] == "placement mismatch"

    def test_missing_or_invalid_prediction_scores_zero(self):
        assert placement_accuracy.evaluate(_ctx({}, {"placement": "center"})).score == 0.0
        assert placement_accuracy.evaluate(_ctx({"placement": "CENTER"}, {"placement": "center"})).score == 0.0

    def test_missing_expected_abstains(self):
        score = placement_accuracy.evaluate(_ctx({"placement": "center"}, {}))
        assert score.score is None
        assert "skipped" in score.metadata


class TestJudgeScorers:
    """The judge scorers share ONE LLM call per output and map distinct Verdict fields."""

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
                    magnitude_value_correct=0.5,
                    overall=0.75,
                    reason="stubbed",
                )

        monkeypatch.setattr(scorers, "_judge", lambda: FakeJudge())
        reset_run_caches()
        return calls

    def test_one_llm_call_shared_across_all_judge_scorers(self, monkeypatch) -> None:
        calls = self._install_counting_judge(monkeypatch)
        ctx = _ctx(_ops_plan({"op": "add_points", "target": "self", "amount": 5}))

        assert intent_match_judge.evaluate(ctx).score == 0.9
        assert target_accuracy.evaluate(ctx).score == 0.7
        assert persistence_accuracy.evaluate(ctx).score == 0.8
        assert magnitude_sign.evaluate(ctx).score == 0.6
        assert magnitude_value.evaluate(ctx).score == 0.5
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

    def test_chosen_card_target_runs(self) -> None:
        output = {
            "verdict": "needs_choice",
            **_ops_plan({"op": "transfer_card", "card_target": "chosen_card", "to_target": "self"}),
        }
        assert executability.evaluate(_ctx(output)).score == 1.0

    def test_real_choice_based_canonical_dry_runs(self) -> None:
        from engine.compile import compile_card_plan

        cards = json.loads((pathlib.Path(__file__).parent.parent / "data" / "seed_cards.json").read_text())
        borrow = next(c for c in cards if c["id"] == "seed-filler-015")
        plan = compile_card_plan({**borrow, "id": "gold", "origin": "seed"})
        output = {"verdict": "needs_choice", "resolution_plan": plan.model_dump()}
        assert executability.evaluate(_ctx(output)).score == 1.0


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

    def test_chooser_target_with_needs_choice_scores_one(self) -> None:
        output = {"verdict": "needs_choice", **_ops_plan({"op": "add_points", "target": "chooser", "amount": 5})}
        assert did_something.evaluate(_ctx(output)).score == 1.0

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


class TestDryRunCacheKeying:
    """Regression: _DRY_RUN_CACHE must be keyed on output content, not
    id(output), so a recycled object address can never serve another
    output's cached report (CPython reuses freed dict addresses)."""

    def test_recycled_id_never_serves_a_stale_report(self) -> None:
        reset_run_caches()
        output_a = {"verdict": "ok", **_ops_plan({"op": "add_points", "target": "self", "amount": 5})}
        report_a = scorers._dry_run_output(output_a)
        assert report_a["ok"] is True

        output_b = {"verdict": "ok", **_ops_plan({"op": "custom_note", "note": "different content"})}
        stale_marker = {"ok": False, "error": "STALE: belongs to output_a, not output_b", "emitted_ops": []}
        # Simulate the pre-fix bug: if output_b's address recycled output_a's
        # freed id, an id()-keyed cache would have this entry under
        # id(output_b) and serve it back unchanged.
        scorers._DRY_RUN_CACHE[id(output_b)] = stale_marker

        report_b = scorers._dry_run_output(output_b)

        assert report_b != stale_marker
        assert report_b["ok"] is True
        assert report_b is not report_a
        reset_run_caches()

    def test_output_key_differs_for_different_content(self) -> None:
        output_a = {"verdict": "ok", **_ops_plan({"op": "add_points", "target": "self", "amount": 5})}
        output_b = {"verdict": "ok", **_ops_plan({"op": "add_points", "target": "self", "amount": 6})}
        assert scorers._output_key(output_a) != scorers._output_key(output_b)


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

    def test_skips_interaction_plans(self) -> None:
        # A free play-time choice can't be aligned with the fixed canonical, so
        # abstain (N/A) rather than score a false 0.
        expected = {"sandbox": "def apply(state, ctx):\n    state.add_points('self', 5)"}
        output = {
            "resolution_plan": {
                "steps": [
                    {
                        "kind": "interaction",
                        "result_key": "who",
                        "request": {
                            "kind": "choice",
                            "prompt": "pick",
                            "audience": "active",
                            "options": [{"id": "a", "label": "A"}],
                        },
                        "input_refs": {},
                    },
                    {"kind": "snippet", "code": "def apply(state, ctx):\n    state.add_points('self', 5)"},
                ]
            }
        }
        score = sandbox_behavior.evaluate(self._ctx(output, expected))
        assert score.score == 1.0
        assert "interaction" in score.metadata["skipped"]

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


class TestDeliberateNoOpCards:
    """Cards whose OWN canonical is a no-op (pure flavour): did_something
    abstains instead of zeroing an unreachable metric, and a plan-less output
    matches an empty expected diff (empty == empty)."""

    _NOOP_EXPECTED = {
        "target": "self",
        "placement": "discard",
        "ops": [{"op": "custom_note", "args": {"note": "flavor only"}}],
        "trigger": None,
        "venue": "all",
        "sandbox": "def apply(state, ctx):\n    state.note('flavor only')",
    }

    def test_did_something_abstains_on_noop_canonical(self) -> None:
        output = {"verdict": "ok", **_ops_plan({"op": "custom_note", "note": "flavor"})}
        score = did_something.evaluate(_ctx(output, expected=self._NOOP_EXPECTED))
        assert score.score is None
        assert "no-op" in score.metadata["skipped"]

    def test_did_something_still_zeroes_noop_answer_to_mechanical_card(self) -> None:
        expected = {
            "target": "self",
            "placement": "discard",
            "ops": [{"op": "add_points", "args": {"target": "self", "amount": 5}}],
            "sandbox": "def apply(state, ctx):\n    state.add_points('self', 5)",
        }
        output = {"verdict": "ok", **_ops_plan({"op": "custom_note", "note": "nothing"})}
        assert did_something.evaluate(_ctx(output, expected=expected)).score == 0.0

    def test_sandbox_behavior_scores_no_plan_as_match_when_expected_diff_empty(self) -> None:
        score = sandbox_behavior.evaluate(_ctx({"verdict": "ok"}, expected=self._NOOP_EXPECTED))
        assert score.score == 1.0

    def test_sandbox_behavior_still_zeroes_no_plan_against_mechanical_sandbox(self) -> None:
        expected = {"sandbox": "def apply(state, ctx):\n    state.add_points('self', 5)"}
        assert sandbox_behavior.evaluate(_ctx({"verdict": "ok"}, expected=expected)).score == 0.0


class TestReactionCards:
    """Reaction plans (counter_play / pending_* reads) dry-run in a synthesized
    reaction window; counter_play counts as a mechanical op."""

    def test_counter_play_ops_plan_executes_and_did_something(self) -> None:
        output = {"verdict": "ok", **_ops_plan({"op": "counter_play", "mode": "steal_hand"})}
        assert executability.evaluate(_ctx(output)).score == 1.0
        assert did_something.evaluate(_ctx(output)).score == 1.0

    def test_reaction_snippet_form_scores_like_ops_form(self) -> None:
        code = "def apply(state, ctx):\n    if ctx.get('pending_card_id'):\n        state.counter_play('steal_hand')\n"
        output = {"verdict": "ok", "resolution_plan": {"steps": [{"kind": "snippet", "code": code}]}}
        assert executability.evaluate(_ctx(output)).score == 1.0
        assert did_something.evaluate(_ctx(output)).score == 1.0
