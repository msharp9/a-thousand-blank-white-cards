#!/usr/bin/env python3
"""Eval harness for the 1000 Blank White Cards interpretation pipeline.

Usage:
    OPENAI_API_KEY=... uv run python -m tbwc.evals.harness [--data PATH] [--limit N]

Loads the real-card testset, runs the compiled agent graph on each card, scores
each output with ALL_SCORERS, and prints a per-dimension table + pipeline metrics.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from tbwc.evals.eval_core import EvalItem, EvalRunReport, run_eval
from tbwc.evals.scorers import ALL_SCORERS

# The scored testset is the hand-annotated gold corpus (has human_canonical
# labels). It lives in eval_cards.json; real_cards.json is the larger raw
# transcription of the full photo album (human_canonical is null there).
DEFAULT_DATA = Path(__file__).resolve().parents[3] / "data" / "eval" / "eval_cards.json"


def load_eval_items(data_path: Path, limit: int | None = None) -> list[EvalItem]:
    cards: list[dict[str, Any]] = json.loads(data_path.read_text(encoding="utf-8"))
    if limit:
        cards = cards[:limit]
    items: list[EvalItem] = []
    for i, card in enumerate(cards):
        title = str(card.get("title", "unknown"))[:20].replace(" ", "_")
        items.append(
            EvalItem(
                id=f"card_{i:03d}_{title}",
                input=card,
                expected=card.get("human_canonical") or {},
                tags=("real_card",),
            )
        )
    return items


def _normalise_graph_output(state: dict[str, Any]) -> dict[str, Any]:
    """Map compiled-graph output keys to the aliases the scorers expect."""
    out: dict[str, Any] = dict(state)
    program = state.get("program")
    if program is not None:
        # EffectProgram -> plain dict for dsl_validity's EffectProgram.model_validate
        out["effect_program"] = program.model_dump() if hasattr(program, "model_dump") else program
    snippet = state.get("snippet")
    if snippet is not None:
        out["snippet_effect"] = getattr(snippet, "code", snippet)
    interp = state.get("interpretation")
    if interp is not None:
        out["classification"] = interp.model_dump() if hasattr(interp, "model_dump") else interp
    return out


def make_task():
    from tbwc.agent.graph import graph

    def task(card: dict[str, Any]) -> dict[str, Any]:
        card_draft = {"title": card.get("title", ""), "description": card.get("description", "")}
        result = graph.invoke({"card_draft": card_draft, "attempts": 0})
        return _normalise_graph_output(result)

    return task


def run_harness(data_path: Path | None = None, limit: int | None = None) -> EvalRunReport:
    """Run the full eval and return the report (also usable programmatically)."""
    items = load_eval_items(data_path or DEFAULT_DATA, limit=limit)
    report = run_eval("tbwc-interpretation", data=items, task=make_task(), scorers=ALL_SCORERS)
    return report


def print_report(report: EvalRunReport) -> None:
    summary = report.summary()
    print("\n=== Eval Summary ===")
    for k, v in summary.items():
        print(f"  {k}: {v}")
    print("\n=== Per-card scores ===")
    for row in report.case_table():
        scores = {s.name: round(row.get(s.name, 0.0), 3) for s in report.scorers}
        print(f"  {row['case_id']}: {scores}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the TBWC interpretation eval harness.")
    parser.add_argument("--data", type=Path, default=DEFAULT_DATA)
    parser.add_argument("--limit", type=int, default=None)
    args = parser.parse_args()
    report = run_harness(args.data, args.limit)
    print_report(report)


if __name__ == "__main__":
    main()
