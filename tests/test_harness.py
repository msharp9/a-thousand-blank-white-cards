"""Tests for the eval harness plumbing (graph + scorers mocked)."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

from tbwc.evals.eval_core import EvalRunReport
from tbwc.evals.harness import _normalise_graph_output, load_eval_items, run_harness
from tbwc.models.effects import AddPointsOp, EffectProgram


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
    out = _normalise_graph_output({"program": prog})
    assert "effect_program" in out
    assert out["effect_program"]["ops"][0]["op"] == "add_points"


def test_run_harness_with_mocked_graph(tmp_path: Path) -> None:
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
    fake_state = {"program": prog, "snippet": None, "interpretation": None, "verdict": None}

    # mock the compiled graph.invoke so no LLM runs; mock the judge-based scorers to avoid API.
    with (
        patch("tbwc.agent.graph.graph.invoke", return_value=fake_state),
        patch("tbwc.evals.scorers._run_judge") as mock_judge,
    ):
        from tbwc.evals.judge import Verdict

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
