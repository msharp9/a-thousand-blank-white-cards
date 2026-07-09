"""Tests for the conclusions renderer (no LLM; uses a synthetic report)."""

from __future__ import annotations

from pathlib import Path

from tbwc.evals.conclusions import render_conclusions_md, write_conclusions
from tbwc.evals.eval_core import EvalItem, Score, create_scorer, run_eval


def _report():
    items = [EvalItem(id="a", input={"title": "t"}, expected={}), EvalItem(id="b", input={"title": "u"}, expected={})]
    scorer = create_scorer("dsl_validity", "structural", lambda ctx: Score(1.0))
    return run_eval("test", data=items, task=lambda x: {"effect_program": {"ops": []}}, scorers=[scorer])


def test_render_contains_sections() -> None:
    md = render_conclusions_md(_report())
    assert "# Eval Conclusions" in md
    assert "Per-dimension scores" in md
    assert "dsl_validity" in md
    assert "Pipeline metrics" in md
    assert "Analysis" in md


def test_write_conclusions(tmp_path: Path) -> None:
    out = tmp_path / "conclusions.md"
    written = write_conclusions(_report(), output_path=out)
    assert written == out
    assert out.exists()
    assert "Eval Conclusions" in out.read_text()
