"""Tests for the few-shot improvement A/B script (run_agent + judge + retriever mocked)."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

from agent.contract import InterpretResult
from evals.eval_core import EvalRunReport
from evals.improvement_ab import render_improvement_table, run_improvement_ab
from models.effects import AddPointsOp, EffectProgram


def test_run_improvement_ab_and_render(tmp_path: Path) -> None:
    data = [
        {"title": "Gain 3", "description": "Gain 3.", "human_canonical": {"placement": "discard", "target": "self"}}
    ]
    p = tmp_path / "cards.json"
    p.write_text(json.dumps(data))
    prog = EffectProgram(ops=[AddPointsOp(target="self", amount=3)])
    fake_result = InterpretResult(program=prog, snippet=None, verdict="ok")
    from evals.judge import Verdict

    verdict = Verdict(
        intent_match=1.0,
        persistence_correct=1.0,
        target_placement_correct=1.0,
        trigger_event_correct=1.0,
        magnitude_sign_correct=1.0,
        overall=1.0,
        reason="ok",
    )
    # dense_retriever is patched so the "after" arm's exemplar priming is deterministic
    # and needs no live store; run_agent is patched so no LLM runs.
    fake_retrieve = lambda query, k=4: [{"card_id": "c1", "title": "T", "description": "d", "canonical": "{}"}]  # noqa: E731
    with (
        patch("agent.runtime.run_agent", return_value=fake_result),
        patch("agent.rag.retrievers.dense_retriever", return_value=fake_retrieve),
        patch("evals.scorers._run_judge", return_value=verdict),
    ):
        before, after = run_improvement_ab(p)
    assert isinstance(before, EvalRunReport)
    table = render_improvement_table(before, after)
    assert "Few-shot improvement" in table
    assert "before" in table
