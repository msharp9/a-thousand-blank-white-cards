"""Tests for the few-shot improvement A/B script (graph + judge mocked)."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

from tbwc.evals.eval_core import EvalRunReport
from tbwc.evals.improvement_ab import render_improvement_table, run_improvement_ab
from tbwc.models.effects import AddPointsOp, EffectProgram


def test_run_improvement_ab_and_render(tmp_path: Path) -> None:
    data = [{"title": "Gain 3", "description": "Gain 3.", "human_canonical": {"timing": "immediate", "target": "self"}}]
    p = tmp_path / "cards.json"
    p.write_text(json.dumps(data))
    prog = EffectProgram(ops=[AddPointsOp(target="self", amount=3)])
    fake_state = {"program": prog, "snippet": None, "interpretation": None, "verdict": None}
    from tbwc.evals.judge import Verdict

    verdict = Verdict(
        intent_match=1.0,
        timing_correct=1.0,
        target_placement_correct=1.0,
        trigger_event_correct=1.0,
        magnitude_sign_correct=1.0,
        overall=1.0,
        reason="ok",
    )
    with (
        patch("tbwc.agent.graph.graph.invoke", return_value=fake_state),
        patch("tbwc.evals.scorers._run_judge", return_value=verdict),
    ):
        before, after = run_improvement_ab(p)
    assert isinstance(before, EvalRunReport)
    table = render_improvement_table(before, after)
    assert "Few-shot improvement" in table
    assert "before" in table
