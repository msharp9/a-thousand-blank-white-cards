#!/usr/bin/env python3
"""Before/after eval: the single agent WITHOUT vs WITH few-shot exemplar priming.

Usage:
    LLM_API_KEY=... uv run python -m evals.improvement_ab [--data PATH] [--limit N]

The old A/B toggled the graph's ``few_shot_exemplars`` config on the ``emit_ops``
node. That node no longer exists — there is a single tool-calling agent
(:func:`agent.runtime.run_agent`) now. This script preserves the *intent* of that
experiment (does injecting retrieved exemplars improve interpretation quality?) by
pre-retrieving the top-k exemplars from the RAG store and prepending them to the
card description for the "after" arm, while the "before" arm runs the bare card.
Both arms go through the same ``run_agent`` entry point.

Prints a Markdown before/after table + ranking.
"""

from __future__ import annotations

import argparse
import logging
from pathlib import Path
from typing import Any

from evals.eval_core import EvalRunReport, compare_eval_reports, run_eval
from evals.harness import DEFAULT_DATA, load_eval_items, normalise_agent_output
from evals.scorers import ALL_SCORERS

logger = logging.getLogger(__name__)

# How many exemplars to prime the "after" arm with (mirrors the old top-3 few-shot).
_N_EXEMPLARS = 3


def _format_exemplars(hits: list[dict[str, Any]]) -> str:
    """Render retrieved exemplar cards into a compact few-shot block."""
    lines = ["Here are similar previously-seen cards and their canonical effects:"]
    for i, hit in enumerate(hits[:_N_EXEMPLARS], start=1):
        title = hit.get("title", "")
        desc = hit.get("description", "")
        canonical = hit.get("canonical", "")
        lines.append(f"Example {i}: {title} — {desc}\n  canonical effect: {canonical}")
    return "\n".join(lines)


def _prime_description(title: str, description: str) -> str:
    """Prepend a retrieved-exemplar few-shot block to the card description.

    Retrieval failures (store not initialised, no OpenAI key, etc.) are non-fatal:
    we log and fall back to the bare description so the arm still runs.
    """
    try:
        from agent.rag.retrievers import dense_retriever

        hits = dense_retriever()(f"{title}\n{description}", _N_EXEMPLARS)
    except Exception as exc:  # noqa: BLE001 — priming is best-effort
        logger.warning("exemplar priming unavailable (non-fatal): %s", exc)
        return description
    if not hits:
        return description
    return f"{_format_exemplars(hits)}\n\n{description}"


def _make_task(few_shot: bool):
    from agent.runtime import run_agent

    def task(card: dict[str, Any]) -> dict[str, Any]:
        title = card.get("title", "")
        description = card.get("description", "")
        if few_shot:
            description = _prime_description(title, description)
        result = run_agent(title, description)
        return normalise_agent_output(result)

    return task


def run_improvement_ab(data_path: Path | None = None, limit: int | None = None) -> tuple[EvalRunReport, EvalRunReport]:
    """Run testset with exemplar priming OFF then ON. Returns (before_report, after_report)."""
    items = load_eval_items(data_path or DEFAULT_DATA, limit=limit)
    before = run_eval("before_no_fewshot", data=items, task=_make_task(False), scorers=ALL_SCORERS)
    after = run_eval("after_fewshot", data=items, task=_make_task(True), scorers=ALL_SCORERS)
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
    parser = argparse.ArgumentParser(description="Few-shot exemplar-priming before/after eval.")
    parser.add_argument("--data", type=Path, default=None)
    parser.add_argument("--limit", type=int, default=None)
    args = parser.parse_args()
    before, after = run_improvement_ab(args.data, args.limit)
    print(render_improvement_table(before, after))
    print("\nRanking (best first):")
    for row in compare_eval_reports(before, after):
        print(f"  {row['evaluation']}: {row}")


if __name__ == "__main__":
    main()
