#!/usr/bin/env python3
"""Before/after eval: emit_ops WITHOUT vs WITH few-shot exemplar injection.

Usage:
    OPENAI_API_KEY=... uv run python -m evals.improvement_ab [--data PATH] [--limit N] [--retriever-mode dense]

Prints a Markdown before/after table + ranking.
"""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

from evals.eval_core import EvalRunReport, compare_eval_reports, run_eval
from evals.harness import DEFAULT_DATA, _normalise_graph_output, load_eval_items
from evals.scorers import ALL_SCORERS


def _make_task(few_shot: bool, retriever_mode: str = "dense"):
    from agent.graph import graph

    config = {"configurable": {"few_shot_exemplars": few_shot, "retriever_mode": retriever_mode}}

    def task(card: dict[str, Any]) -> dict[str, Any]:
        card_draft = {"title": card.get("title", ""), "description": card.get("description", "")}
        result = graph.invoke({"card_draft": card_draft, "attempts": 0}, config=config)
        return _normalise_graph_output(result)

    return task


def run_improvement_ab(
    data_path: Path | None = None, limit: int | None = None, retriever_mode: str = "dense"
) -> tuple[EvalRunReport, EvalRunReport]:
    """Run testset with few-shot OFF then ON. Returns (before_report, after_report)."""
    items = load_eval_items(data_path or DEFAULT_DATA, limit=limit)
    before = run_eval("before_no_fewshot", data=items, task=_make_task(False, retriever_mode), scorers=ALL_SCORERS)
    after = run_eval("after_fewshot", data=items, task=_make_task(True, retriever_mode), scorers=ALL_SCORERS)
    return before, after


def render_improvement_table(before: EvalRunReport, after: EvalRunReport) -> str:
    b, a = before.summary(), after.summary()
    metrics = [s.name for s in before.scorers] + ["mean_task_latency_ms"]
    lines = [
        "# Few-shot improvement — before vs after",
        "",
        "| Metric | before (no few-shot) | after (few-shot) | delta |",
        "| --- | ---: | ---: | ---: |",
    ]
    for m in metrics:
        bv, av = b.get(m, 0.0), a.get(m, 0.0)
        lines.append(f"| {m} | {bv:.3f} | {av:.3f} | {av - bv:+.3f} |")
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description="Few-shot before/after eval.")
    parser.add_argument("--data", type=Path, default=None)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--retriever-mode", default="dense")
    args = parser.parse_args()
    before, after = run_improvement_ab(args.data, args.limit, args.retriever_mode)
    print(render_improvement_table(before, after))
    print("\nRanking (best first):")
    for row in compare_eval_reports(before, after):
        print(f"  {row['evaluation']}: {row}")


if __name__ == "__main__":
    main()
