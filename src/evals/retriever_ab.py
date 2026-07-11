#!/usr/bin/env python3
"""Retriever A/B: baseline dense vs multi-query advanced, over the real-card testset.

Usage:
    LLM_API_KEY=... uv run python -m evals.retriever_ab [--data PATH] [--limit N]

The old version drove the whole legacy pipeline twice via a ``retriever_mode`` config
key that only that pipeline understood. The single agent (:func:`agent.runtime.run_agent`)
has no such knob, so this script now compares the two retrievers DIRECTLY —
``agent.rag.retrievers.dense_retriever`` vs ``advanced_retriever`` — and scores
*retrieval quality* rather than downstream interpretation. This is the truest test
of the bead's intent (does multi-query expansion surface better exemplars?), it is
fully deterministic (no LLM judge), and it does not depend on the retired graph.

Scored dimensions (all structural, no LLM):
  - recall_nonempty:  did the retriever return at least one exemplar?
  - timing_match:     does any retrieved exemplar's canonical timing match the
                      gold card's expected timing?
  - target_match:     does any retrieved exemplar's canonical target match expected?

Prints per-dimension mean scores + latency for each retriever, and a comparison
ranking.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from evals.eval_core import EvalRunReport, Score, ScorerContext, compare_eval_reports, create_scorer, run_eval
from evals.harness import DEFAULT_DATA, load_eval_items


def _canonical_of(hit: dict[str, Any]) -> dict[str, Any]:
    """Parse a retrieved exemplar's canonical payload (a JSON string) into a dict."""
    canonical = hit.get("canonical")
    if isinstance(canonical, dict):
        return canonical
    if isinstance(canonical, str) and canonical.strip():
        try:
            parsed = json.loads(canonical)
            return parsed if isinstance(parsed, dict) else {}
        except json.JSONDecodeError:
            return {}
    return {}


def _recall_nonempty_scorer(context: ScorerContext) -> Score:
    hits = (context.output or {}).get("hits") or []
    return Score(score=1.0 if hits else 0.0, metadata={"n_hits": len(hits)})


def _field_match_scorer(field: str):
    """Build a scorer: 1.0 if any retrieved exemplar's canonical[field] == expected[field]."""

    def _scorer(context: ScorerContext) -> Score:
        expected = context.expected or {}
        want = expected.get(field)
        if want is None:
            # No gold label for this field -> treat as N/A (neutral pass).
            return Score(score=1.0, metadata={"reason": f"no expected {field}"})
        hits = (context.output or {}).get("hits") or []
        for hit in hits:
            if _canonical_of(hit).get(field) == want:
                return Score(score=1.0, metadata={"matched": want})
        return Score(score=0.0, metadata={"want": want})

    return _scorer


recall_nonempty = create_scorer(
    name="recall_nonempty",
    description="Did the retriever return at least one exemplar?",
    scorer=_recall_nonempty_scorer,
)
timing_match = create_scorer(
    name="timing_match",
    description="Does any retrieved exemplar's canonical timing match the gold card?",
    scorer=_field_match_scorer("timing"),
)
target_match = create_scorer(
    name="target_match",
    description="Does any retrieved exemplar's canonical target match the gold card?",
    scorer=_field_match_scorer("target"),
)

RETRIEVAL_SCORERS = [recall_nonempty, timing_match, target_match]


def _make_task(mode: str):
    """Return a task that retrieves exemplars for a card using the given retriever."""
    from agent.rag.retrievers import advanced_retriever, dense_retriever

    retrieve = advanced_retriever() if mode == "advanced" else dense_retriever()

    def task(card: dict[str, Any]) -> dict[str, Any]:
        query = f"{card.get('title', '')}\n{card.get('description', '')}"
        try:
            hits = retrieve(query, 4)
        except Exception as exc:  # noqa: BLE001 — an unavailable store yields no hits, not a crash
            return {"hits": [], "error": str(exc)}
        return {"hits": list(hits)}

    return task


def run_ab(data_path: Path | None = None, limit: int | None = None) -> tuple[EvalRunReport, EvalRunReport]:
    """Run the testset under the dense and advanced retrievers; return (dense_report, advanced_report)."""
    items = load_eval_items(data_path or DEFAULT_DATA, limit=limit)
    dense = run_eval("dense", data=items, task=_make_task("dense"), scorers=RETRIEVAL_SCORERS)
    advanced = run_eval("advanced", data=items, task=_make_task("advanced"), scorers=RETRIEVAL_SCORERS)
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
    parser = argparse.ArgumentParser(description="Retriever A/B comparison (dense vs advanced multi-query).")
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
