#!/usr/bin/env python3
"""Eval harness for the 1000 Blank White Cards interpretation pipeline.

Usage:
    LLM_API_KEY=... uv run python -m evals.harness [--data PATH] [--limit N]

Loads the real-card testset, runs the single tool-calling agent
(:func:`agent.runtime.run_agent`) on each card, scores each output with
ALL_SCORERS, and prints a per-dimension table + pipeline metrics.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from evals.eval_core import EvalItem, EvalRunReport, run_eval
from evals.paths import find_repo_root
from evals.scorers import ALL_SCORERS

# The scored testset is the hand-annotated gold corpus (has human_canonical
# labels). It lives in eval_cards.json; real_cards.json is the larger raw
# transcription of the full photo album (human_canonical is null there).
DEFAULT_DATA = find_repo_root(Path(__file__)) / "data" / "eval" / "eval_cards.json"


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


def normalise_agent_output(result: Any) -> dict[str, Any]:
    """Map an :class:`~agent.contract.InterpretResult` to the dict the scorers read.

    The scorers (see evals.scorers) consume three keys:
      - ``effect_program``: the EffectProgram as a plain dict (dsl_validity re-validates
        it, the judge summarises it). Produced from ``result.program``.
      - ``snippet_effect``: the generated Python hook body, if any. Produced from
        ``result.snippet.code``.
      - ``verdict``: the agent's overall verdict string ("ok"/"invalid"/"needs_choice").

    Note: the old graph exposed a "classification" dict (from a dedicated classify
    node) that the judge could fall back to. The new single agent has no separate
    classify step, so there is no "classification" key to derive; the judge instead
    summarises the effect_program / snippet directly (both richer signals of intent),
    which is why _effect_summary already prefers those. No sub-metric is lost.
    """
    program = getattr(result, "program", None)
    snippet = getattr(result, "snippet", None)
    out: dict[str, Any] = {
        "verdict": getattr(result, "verdict", None),
        "comment": getattr(result, "comment", ""),
        "persona_action": getattr(result, "persona_action", "none"),
    }
    if program is not None:
        # EffectProgram -> plain dict for dsl_validity's EffectProgram.model_validate.
        out["effect_program"] = program.model_dump() if hasattr(program, "model_dump") else program
    if snippet is not None:
        out["snippet_effect"] = getattr(snippet, "code", snippet)
    return out


def make_task():
    from agent.runtime import run_agent

    def task(card: dict[str, Any]) -> dict[str, Any]:
        # Eval cards have no live game state, so state/actor are None; run_agent
        # never raises or hangs, always returning a well-formed InterpretResult.
        result = run_agent(card.get("title", ""), card.get("description", ""))
        return normalise_agent_output(result)

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
