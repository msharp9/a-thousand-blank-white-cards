#!/usr/bin/env python3
"""Retriever A/B: baseline dense vs multi-query advanced, over the real-card testset.

Usage:
    OPENAI_API_KEY=... uv run python -m evals.retriever_ab [--data PATH] [--limit N]

Prints per-dimension mean scores + dsl_validity% + latency for each retriever mode,
and a comparison ranking.
"""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

from evals.eval_core import EvalRunReport, compare_eval_reports, run_eval
from evals.harness import DEFAULT_DATA, _normalise_graph_output, load_eval_items
from evals.scorers import ALL_SCORERS


def _make_task(mode: str):
    """Return a task that runs the graph with the given retriever_mode."""
    from agent.graph import graph

    config = {"configurable": {"retriever_mode": mode}}

    def task(card: dict[str, Any]) -> dict[str, Any]:
        card_draft = {"title": card.get("title", ""), "description": card.get("description", "")}
        result = graph.invoke({"card_draft": card_draft, "attempts": 0}, config=config)
        return _normalise_graph_output(result)

    return task


def run_ab(data_path: Path | None = None, limit: int | None = None) -> tuple[EvalRunReport, EvalRunReport]:
    """Run the testset under dense and advanced retrievers; return (dense_report, advanced_report)."""
    items = load_eval_items(data_path or DEFAULT_DATA, limit=limit)
    dense = run_eval("dense", data=items, task=_make_task("dense"), scorers=ALL_SCORERS)
    advanced = run_eval("advanced", data=items, task=_make_task("advanced"), scorers=ALL_SCORERS)
    return dense, advanced


def render_ab_table(dense: EvalRunReport, advanced: EvalRunReport) -> str:
    """Render a Markdown comparison table of the two reports."""
    ds, ad = dense.summary(), advanced.summary()
    metrics = [s.name for s in dense.scorers] + ["mean_task_latency_ms"]
    lines = [
        "# Retriever A/B — dense vs advanced (multi-query)",
        "",
        "| Metric | dense | advanced | delta |",
        "| --- | ---: | ---: | ---: |",
    ]
    for m in metrics:
        d = ds.get(m, 0.0)
        a = ad.get(m, 0.0)
        lines.append(f"| {m} | {d:.3f} | {a:.3f} | {a - d:+.3f} |")
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description="Retriever A/B comparison.")
    parser.add_argument("--data", type=Path, default=None)
    parser.add_argument("--limit", type=int, default=None)
    args = parser.parse_args()
    dense, advanced = run_ab(args.data, args.limit)
    print(render_ab_table(dense, advanced))
    print("\nRanking (best first):")
    for row in compare_eval_reports(dense, advanced):
        print(f"  {row['evaluation']}: {row}")


if __name__ == "__main__":
    main()
