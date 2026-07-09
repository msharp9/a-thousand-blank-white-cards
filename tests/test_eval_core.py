"""Smoke tests for tbwc.evals.eval_core."""

from __future__ import annotations

import pytest

from tbwc.evals.eval_core import (
    EvalItem,
    EvalRunReport,
    Score,
    Scorer,
    compare_eval_reports,
    create_scorer,
    run_eval,
)


def _exact_scorer() -> Scorer:
    return create_scorer("exact", "exact match", lambda ctx: Score(1.0 if ctx.output == ctx.expected else 0.0))


def test_run_eval_basic() -> None:
    items = [EvalItem(id="a", input=1, expected=1), EvalItem(id="b", input=2, expected=2)]
    report = run_eval("smoke", data=items, task=lambda x: x, scorers=[_exact_scorer()])
    assert isinstance(report, EvalRunReport)
    s = report.summary()
    assert s["exact"] == 1.0
    assert s["cases"] == 2


def test_case_table_is_list_of_dicts() -> None:
    items = [EvalItem(id="a", input=1, expected=1)]
    report = run_eval("smoke", data=items, task=lambda x: x, scorers=[_exact_scorer()])
    table = report.case_table()
    assert isinstance(table, list)
    assert table[0]["case_id"] == "a"
    assert table[0]["exact"] == 1.0


def test_scorer_can_return_float() -> None:
    items = [EvalItem(id="a", input=1, expected=2)]
    report = run_eval("f", data=items, task=lambda x: x, scorers=[create_scorer("half", "d", lambda ctx: 0.5)])
    assert report.summary()["half"] == 0.5


def test_compare_reports_orders_by_score() -> None:
    items = [EvalItem(id="a", input=1, expected=1)]
    good = run_eval("good", data=items, task=lambda x: x, scorers=[_exact_scorer()])
    bad = run_eval("bad", data=items, task=lambda x: 999, scorers=[_exact_scorer()])
    ranked = compare_eval_reports(good, bad)
    assert ranked[0]["evaluation"] == "good"


def test_invalid_score_raises() -> None:
    with pytest.raises(ValueError):
        Score(1.5)


def test_run_eval_requires_data() -> None:
    with pytest.raises(ValueError):
        run_eval("x", data=[], task=lambda x: x, scorers=[_exact_scorer()])
