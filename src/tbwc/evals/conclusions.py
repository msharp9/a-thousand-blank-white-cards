#!/usr/bin/env python3
"""Run the eval harness and write a conclusions report to data/eval/conclusions.md.

Usage:
    OPENAI_API_KEY=... uv run python -m tbwc.evals.conclusions [--data PATH] [--limit N]
"""

from __future__ import annotations

import argparse
from pathlib import Path

from tbwc.evals.eval_core import EvalRunReport
from tbwc.evals.harness import run_harness

OUTPUT_PATH = Path(__file__).resolve().parents[3] / "data" / "eval" / "conclusions.md"


def render_conclusions_md(report: EvalRunReport) -> str:
    """Render a Markdown conclusions document from an EvalRunReport."""
    summary = report.summary()
    scorer_names = [s.name for s in report.scorers]
    n = summary["cases"]

    score_rows = "\n".join(f"| {name} | {summary.get(name, 0.0):.3f} |" for name in scorer_names)

    # Pipeline metrics
    table = report.case_table()
    dsl_scores = [row.get("dsl_validity", 0.0) for row in table]
    valid_pct = (sum(1 for s in dsl_scores if s >= 1.0) / len(dsl_scores) * 100) if dsl_scores else 0.0
    mean_task_ms = summary.get("mean_task_latency_ms", 0.0)

    lines = [
        "# Eval Conclusions — TBWC Card Interpretation",
        "",
        f"Evaluated **{n}** hand-annotated gold cards from `data/eval/eval_cards.json`.",
        "",
        "## Per-dimension scores",
        "",
        "| Dimension | Mean score (0–1) |",
        "| --- | --- |",
        score_rows,
        "",
        "## Pipeline metrics",
        "",
        f"- Valid EffectProgram (dsl_validity == 1.0): **{valid_pct:.1f}%** of cards",
        f"- Mean task latency: **{mean_task_ms:.1f} ms/card**",
        "",
        "## Analysis",
        "",
        _analysis_paragraph(summary, scorer_names, valid_pct),
        "",
    ]
    return "\n".join(lines)


def _analysis_paragraph(summary: dict, scorer_names: list[str], valid_pct: float) -> str:
    """Produce a short data-driven analysis paragraph."""
    judged = [name for name in scorer_names if name != "dsl_validity"]
    if judged:
        best = max(judged, key=lambda name: summary.get(name, 0.0))
        worst = min(judged, key=lambda name: summary.get(name, 0.0))
        return (
            f"The pipeline scores highest on **{best}** ({summary.get(best, 0.0):.3f}) and lowest on "
            f"**{worst}** ({summary.get(worst, 0.0):.3f}). {valid_pct:.1f}% of interpretations produced a "
            "structurally valid EffectProgram. Weaker dimensions indicate where prompt tuning or few-shot "
            "exemplars (Phase 6) should focus."
        )
    return f"{valid_pct:.1f}% of interpretations produced a structurally valid EffectProgram."


def write_conclusions(report: EvalRunReport, output_path: Path | None = None) -> Path:
    """Render and write the conclusions markdown; return the path written."""
    path = output_path or OUTPUT_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_conclusions_md(report), encoding="utf-8")
    return path


def main() -> None:
    parser = argparse.ArgumentParser(description="Run evals and write conclusions.md.")
    parser.add_argument("--data", type=Path, default=None)
    parser.add_argument("--limit", type=int, default=None)
    args = parser.parse_args()
    report = run_harness(args.data, args.limit)
    path = write_conclusions(report)
    print(f"Wrote {path}")
    print(report.summary())


if __name__ == "__main__":
    main()
