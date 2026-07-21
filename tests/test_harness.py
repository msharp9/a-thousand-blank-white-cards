"""Tests for the eval harness plumbing (run_agent + judge mocked)."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

from agent.contract import InterpretResult
from evals.eval_core import EvalRunReport
from evals.harness import load_eval_items, normalise_agent_output, run_harness
from models.effects import AddPointsOp, EffectProgram, OpsStep, ResolutionPlan, SnippetStep


def test_load_eval_items(tmp_path: Path) -> None:
    data = [{"title": "T1", "description": "d1", "human_canonical": {"placement": "discard"}}]
    p = tmp_path / "cards.json"
    p.write_text(json.dumps(data))
    items = load_eval_items(p)
    assert len(items) == 1
    assert items[0].input["title"] == "T1"
    assert items[0].expected == {"placement": "discard"}


def test_load_suite_items_all_combines_gold_and_hard() -> None:
    from evals.harness import load_suite_items

    items = load_suite_items("all", limit=2)
    assert len(items) == 4
    assert {t for item in items for t in item.tags} == {"real_card", "hard_card"}


def test_normalise_folds_legacy_program_into_resolution_plan() -> None:
    prog = EffectProgram(ops=[AddPointsOp(target="self", amount=3)])
    result = InterpretResult(program=prog, verdict="ok")
    out = normalise_agent_output(result)
    steps = out["resolution_plan"]["steps"]
    assert steps[0]["kind"] == "ops"
    assert steps[0]["ops"][0]["op"] == "add_points"
    assert out["verdict"] == "ok"
    assert "effect_program" not in out
    assert "snippet_effect" not in out


def test_normalise_omits_plan_when_no_effect() -> None:
    out = normalise_agent_output(InterpretResult(verdict="invalid", comment="nope"))
    assert "resolution_plan" not in out
    assert out["verdict"] == "invalid"


def test_normalise_passes_agent_error_flag() -> None:
    assert normalise_agent_output(InterpretResult(verdict="invalid", agent_error=True))["agent_error"] is True
    assert normalise_agent_output(InterpretResult(verdict="ok"))["agent_error"] is False


def test_normalise_preserves_complete_mixed_resolution_plan() -> None:
    plan = ResolutionPlan(
        steps=[
            OpsStep(ops=[AddPointsOp(target="self", amount=1)]),
            SnippetStep(code="def apply(state, ctx):\n    state.add_points('self', 2)\n"),
        ]
    )
    result = InterpretResult(plan=plan, verdict="ok")

    out = normalise_agent_output(result)

    assert [step["kind"] for step in out["resolution_plan"]["steps"]] == ["ops", "snippet"]
    assert "state.add_points" in out["resolution_plan"]["steps"][1]["code"]


class TestRecoverPlanFromComment:
    _PLAN_JSON = '{"steps": [{"kind": "ops", "ops": [{"op": "add_points", "target": "self", "amount": 5}]}]}'

    def _out(self, comment: str, verdict: str = "invalid") -> dict:
        return normalise_agent_output(InterpretResult(verdict=verdict, comment=comment, agent_error=True))

    def test_recovers_bare_plan_object(self) -> None:
        out = self._out(f'{{"plan": {self._PLAN_JSON}}}')
        assert out["resolution_plan"]["steps"][0]["ops"][0]["op"] == "add_points"
        assert out["plan_recovered_from_comment"] is True
        assert out["verdict"] == "ok"
        assert out["raw_verdict"] == "invalid"

    def test_recovers_from_fenced_block_with_prose(self) -> None:
        comment = f'Here is my answer:\n```json\n{{"resolution_plan": {self._PLAN_JSON}}}\n```\nHope that helps!'
        out = self._out(comment)
        assert out["resolution_plan"]["steps"][0]["ops"][0]["op"] == "add_points"
        assert out["plan_recovered_from_comment"] is True

    def test_recovers_bare_steps_shape(self) -> None:
        out = self._out(self._PLAN_JSON)
        assert out["resolution_plan"]["steps"][0]["ops"][0]["op"] == "add_points"

    def test_truncated_json_recovers_nothing(self) -> None:
        out = self._out('{"plan": {"steps": [{"kind": "ops", "ops": [{"op": "add_poi')
        assert "resolution_plan" not in out
        assert "plan_recovered_from_comment" not in out
        assert out["verdict"] == "invalid"

    def test_plain_prose_is_untouched(self) -> None:
        out = self._out("This card does nothing interesting.")
        assert "resolution_plan" not in out
        assert out["verdict"] == "invalid"

    def test_real_plan_wins_over_comment_json(self) -> None:
        prog = EffectProgram(ops=[AddPointsOp(target="self", amount=1)])
        result = InterpretResult(program=prog, verdict="ok", comment=f'{{"plan": {self._PLAN_JSON}}}')
        out = normalise_agent_output(result)
        assert out["resolution_plan"]["steps"][0]["ops"][0]["amount"] == 1
        assert "plan_recovered_from_comment" not in out

    def test_verdict_preserved_when_not_invalid(self) -> None:
        out = self._out(f'{{"plan": {self._PLAN_JSON}}}', verdict="needs_choice")
        assert out["plan_recovered_from_comment"] is True
        assert out["verdict"] == "needs_choice"
        assert "raw_verdict" not in out


def test_run_harness_with_mocked_agent(tmp_path: Path) -> None:
    data = [
        {
            "title": "Gain 3",
            "description": "Gain 3 points.",
            "human_canonical": {"placement": "discard", "target": "self"},
        },
    ]
    p = tmp_path / "cards.json"
    p.write_text(json.dumps(data))

    prog = EffectProgram(ops=[AddPointsOp(target="self", amount=3)])
    fake_result = InterpretResult(program=prog, snippet=None, verdict="ok")

    # mock run_agent so no LLM runs; mock the judge-based scorers to avoid API.
    with (
        patch("agent.runtime.run_agent", return_value=fake_result),
        patch("evals.scorers._run_judge") as mock_judge,
    ):
        from evals.judge import Verdict

        mock_judge.return_value = Verdict(
            intent_match=1.0,
            persistence_correct=1.0,
            target_placement_correct=1.0,
            trigger_event_correct=1.0,
            magnitude_sign_correct=1.0,
            overall=1.0,
            reason="ok",
        )
        report = run_harness(p)
    assert isinstance(report, EvalRunReport)
    summary = report.summary()
    assert summary["cases"] == 1
    assert summary["dsl_validity"] == 1.0
