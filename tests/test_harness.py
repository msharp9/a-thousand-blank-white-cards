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
    data = [{"title": "T1", "description": "d1", "human_canonical": {"timing": "immediate"}}]
    p = tmp_path / "cards.json"
    p.write_text(json.dumps(data))
    items = load_eval_items(p)
    assert len(items) == 1
    assert items[0].input["title"] == "T1"
    assert items[0].expected == {"timing": "immediate"}


def test_normalise_maps_program_to_effect_program() -> None:
    prog = EffectProgram(ops=[AddPointsOp(target="self", amount=3)])
    result = InterpretResult(program=prog, verdict="ok")
    out = normalise_agent_output(result)
    assert "effect_program" in out
    assert out["effect_program"]["ops"][0]["op"] == "add_points"
    assert out["resolution_plan"]["steps"][0]["kind"] == "ops"
    assert out["verdict"] == "ok"


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


def test_run_harness_with_mocked_agent(tmp_path: Path) -> None:
    data = [
        {
            "title": "Gain 3",
            "description": "Gain 3 points.",
            "human_canonical": {"timing": "immediate", "target": "self"},
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
            timing_correct=1.0,
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
    # dsl_validity should be 1.0 (valid non-empty EffectProgram)
    assert summary["dsl_validity"] == 1.0
