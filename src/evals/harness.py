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
import re
from pathlib import Path
from typing import Any

from evals.eval_core import EvalItem, EvalRunReport, run_eval
from evals.paths import find_repo_root
from evals.scorers import ALL_SCORERS

# The scored testsets, all hand-annotated with human_canonical labels:
#   gold — eval_cards.json, the broad coverage set.
#   hard — eval_cards_hard.json, compositional sandbox/steps-only cards that
#          stretch the agent (ops: null by design).
# real_cards.json is the larger raw photo transcription, not a scored suite.
DEFAULT_DATA = find_repo_root(Path(__file__)) / "data" / "eval" / "eval_cards.json"
DEFAULT_HARD_DATA = find_repo_root(Path(__file__)) / "data" / "eval" / "eval_cards_hard.json"

SUITES: dict[str, list[tuple[Path, str]]] = {
    "gold": [(DEFAULT_DATA, "real_card")],
    "hard": [(DEFAULT_HARD_DATA, "hard_card")],
    "all": [(DEFAULT_DATA, "real_card"), (DEFAULT_HARD_DATA, "hard_card")],
}


def load_eval_items(data_path: Path, limit: int | None = None, tag: str = "real_card") -> list[EvalItem]:
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
                tags=(tag,),
            )
        )
    return items


def load_suite_items(suite: str, limit: int | None = None) -> list[EvalItem]:
    items: list[EvalItem] = []
    for path, tag in SUITES[suite]:
        items.extend(load_eval_items(path, limit=limit, tag=tag))
    return items


def _iter_json_objects(text: str):
    """Yield each top-level JSON object embedded in ``text``.

    Strips markdown code fences, then scans from every ``{`` with
    ``raw_decode`` so leading/trailing prose is tolerated.
    """
    cleaned = re.sub(r"```(?:json)?", "", text)
    decoder = json.JSONDecoder()
    index, length = 0, len(cleaned)
    while index < length:
        if cleaned[index] != "{":
            index += 1
            continue
        try:
            obj, end = decoder.raw_decode(cleaned, index)
        except json.JSONDecodeError:
            index += 1
            continue
        yield obj
        index = end


def _recover_plan_from_comment(comment: str) -> tuple[Any, str | None]:
    """Best-effort recovery of a plan the agent emitted as prose in ``comment``.

    The runtime fallback collapses an unparsed final message into ``comment``;
    when that message actually carried a valid plan (agent got the answer right
    but shaped its output wrong) recovery rescues it rather than scoring the card
    invalid. Returns ``(plan, embedded_verdict)`` — either may be ``None`` — and
    never raises.
    """
    if not isinstance(comment, str) or "{" not in comment:
        return None, None

    from agent.contract import InterpretResult
    from models.effects import ResolutionPlan

    def _as_plan(candidate: Any):
        if not isinstance(candidate, dict):
            return None
        try:
            return ResolutionPlan.model_validate(candidate)
        except Exception:  # noqa: BLE001 — a malformed candidate is simply not a plan
            return None

    for payload in _iter_json_objects(comment):
        if not isinstance(payload, dict):
            continue
        candidates = [
            _as_plan(payload.get("plan")),
            _as_plan(payload.get("resolution_plan")),
            _as_plan(payload) if "steps" in payload else None,
        ]
        if any(key in payload for key in ("plan", "program", "snippet")):
            try:
                candidates.append(InterpretResult.model_validate(payload).to_plan())
            except Exception:  # noqa: BLE001 — not a contract-shaped payload
                pass
        for plan in candidates:
            if plan is not None and plan.steps:
                verdict = payload.get("verdict")
                return plan, verdict if isinstance(verdict, str) else None
    return None, None


def normalise_agent_output(result: Any) -> dict[str, Any]:
    """Map an :class:`~agent.contract.InterpretResult` to the dict the scorers read.

    ``to_plan()`` folds any legacy program/snippet into the ordered plan, so
    ``resolution_plan`` is the single mechanical form scorers consume; it is
    omitted when the agent produced no effect (e.g. verdict="invalid"). When no
    plan is produced, a last resort tries to recover one the agent mistakenly
    emitted as prose in ``comment``.
    """
    out: dict[str, Any] = {
        "verdict": getattr(result, "verdict", None),
        "comment": getattr(result, "comment", ""),
        "persona_action": getattr(result, "persona_action", "none"),
        "agent_error": bool(getattr(result, "agent_error", False)),
    }
    to_plan = getattr(result, "to_plan", None)
    if callable(to_plan):
        plan = to_plan()
        if plan.steps:
            out["resolution_plan"] = plan.model_dump()

    if "resolution_plan" not in out:
        recovered, embedded_verdict = _recover_plan_from_comment(out["comment"])
        if recovered is not None:
            out["resolution_plan"] = recovered.model_dump()
            out["plan_recovered_from_comment"] = True
            if out.get("verdict") == "invalid":
                out["raw_verdict"] = "invalid"
                out["verdict"] = embedded_verdict if embedded_verdict and embedded_verdict != "invalid" else "ok"
    return out


def make_task():
    from agent.runtime import run_agent

    def task(card: dict[str, Any]) -> dict[str, Any]:
        # Eval cards have no live game state, so state/actor are None; run_agent
        # never raises or hangs, always returning a well-formed InterpretResult.
        result = run_agent(card.get("title", ""), card.get("description", ""))
        return normalise_agent_output(result)

    return task


def run_harness(data_path: Path | None = None, limit: int | None = None, suite: str | None = None) -> EvalRunReport:
    """Run the full eval and return the report (also usable programmatically).

    ``suite`` ("gold"/"hard"/"all") selects the standard testsets; an explicit
    ``data_path`` overrides it for ad-hoc files.
    """
    from evals.scorers import reset_run_caches

    reset_run_caches()
    if data_path is not None:
        items = load_eval_items(data_path, limit=limit)
    else:
        items = load_suite_items(suite or "gold", limit=limit)
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
    parser.add_argument("--data", type=Path, default=None, help="explicit dataset path (overrides --suite)")
    parser.add_argument("--suite", choices=sorted(SUITES), default="gold")
    parser.add_argument("--limit", type=int, default=None)
    args = parser.parse_args()
    report = run_harness(args.data, args.limit, suite=args.suite)
    print_report(report)


if __name__ == "__main__":
    main()
