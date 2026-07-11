"""Tests for the retriever A/B script (retrievers mocked, no agent, no LLM)."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

from evals.eval_core import EvalRunReport
from evals.retriever_ab import render_ab_table, run_ab


def test_run_ab_and_render(tmp_path: Path) -> None:
    data = [{"title": "Gain 3", "description": "Gain 3.", "human_canonical": {"timing": "immediate", "target": "self"}}]
    p = tmp_path / "cards.json"
    p.write_text(json.dumps(data))

    # dense returns a matching exemplar; advanced returns a non-matching one, so the
    # A/B produces different, meaningful scores without any LLM or live store.
    dense_hits = [
        {
            "card_id": "c1",
            "title": "Gain",
            "description": "d",
            "canonical": json.dumps({"timing": "immediate", "target": "self"}),
        }
    ]
    advanced_hits = [
        {
            "card_id": "c2",
            "title": "Lose",
            "description": "d",
            "canonical": json.dumps({"timing": "persistent", "target": "all"}),
        }
    ]

    def fake_dense(query, k=4):
        return dense_hits

    def fake_advanced(query, k=4):
        return advanced_hits

    with (
        patch("agent.rag.retrievers.dense_retriever", return_value=fake_dense),
        patch("agent.rag.retrievers.advanced_retriever", return_value=fake_advanced),
    ):
        dense, advanced = run_ab(p)

    assert isinstance(dense, EvalRunReport)
    assert isinstance(advanced, EvalRunReport)

    ds, ad = dense.summary(), advanced.summary()
    # dense exemplar matches the gold timing/target; advanced does not.
    assert ds["recall_nonempty"] == 1.0
    assert ds["timing_match"] == 1.0
    assert ds["target_match"] == 1.0
    assert ad["recall_nonempty"] == 1.0
    assert ad["timing_match"] == 0.0
    assert ad["target_match"] == 0.0

    table = render_ab_table(dense, advanced)
    assert "Retriever A/B" in table
    assert "dense" in table
    assert "advanced" in table
    assert "recall_nonempty" in table
