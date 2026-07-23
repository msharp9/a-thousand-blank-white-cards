"""evals.analysis — tidy DataFrames and aggregations over persisted eval runs.

Consumes the payload dicts produced by ``evals.store.load_runs`` and reshapes
them for question-first analysis (the ``scripts/analyze_evals.ipynb`` notebook):
one frame of runs, one frame of per-card rows, plus failure-mode bucketing so
"why did this card fail" can be counted instead of eyeballed.

Kept out of the notebook so the notebook stays a thin driver and these are
unit-testable.
"""

from __future__ import annotations

import re
from typing import Any

from evals.viz import QUALITY_METRICS

ALL_METRICS = ["dsl_validity", *QUALITY_METRICS]

# A dimension "fails" below this. Judge metrics are 0/1 in practice;
# sandbox_behavior is fractional (multiset Jaccard), so use a midpoint cut.
FAIL_THRESHOLD = 0.5

# Ordered failure buckets, coarse → specific. A row can carry several; the
# FIRST one present is its *primary* mode, so order encodes the causal chain
# (an agent error explains a missing plan, a missing plan explains judge zeros).
FAILURE_BUCKETS = [
    "agent_error",  # the agent crashed / hit a cap and fell back
    "declared_invalid",  # agent ruled the card invalid → no effect
    "needs_choice",  # agent stopped to ask for a player choice
    "no_plan",  # verdict ok but no/empty resolution_plan
    "invalid_dsl",  # plan present but fails static sandbox validation
    "unresolved_choice",  # plan needs ctx.chosen_* that no one supplied
    "runtime_error",  # plan compiles but crashes the dry-run
    "no_op",  # runs cleanly but changes no game state
    "snippet_crash",  # generated snippet raises on the behavior fixtures
    "behavior_mismatch",  # executes but its op diff diverges from canonical
    "wrong_intent",  # judge: effect does not do what the card says
    "wrong_target",  # judge: hits the wrong player(s)/placement
    "wrong_persistence",  # judge: one-shot vs ongoing / trigger wrong
    "wrong_magnitude",  # judge: helps/hurts in the wrong direction
]

_JUDGE_BUCKETS = {
    "wrong_intent": "intent_match",
    "wrong_target": "target_accuracy",
    "wrong_persistence": "persistence_accuracy",
    "wrong_magnitude": "magnitude_sign",
}


def short_model(model_id: str | None) -> str:
    """Human label for a gateway model id: ``us.anthropic.claude-sonnet-5`` → ``sonnet-5``."""
    name = (model_id or "default").rsplit(".", 1)[-1]
    name = name.removeprefix("claude-")
    return re.sub(r"-\d{8}.*$", "", name)


def _meta_reason(row: dict[str, Any], metric: str) -> str:
    meta = (row.get("score_meta") or {}).get(metric) or {}
    return str(meta.get("reason") or "")


def _score(row: dict[str, Any], metric: str) -> float | None:
    value = (row.get("scores") or {}).get(metric)
    return None if value is None else float(value)


def quality_score(scores: dict[str, Any]) -> float | None:
    """Composite 0–1 quality: mean of the QUALITY_METRICS present on a row."""
    values = [float(scores[m]) for m in QUALITY_METRICS if scores.get(m) is not None]
    return sum(values) / len(values) if values else None


def failure_buckets(row: dict[str, Any]) -> list[str]:
    """All failure buckets a row hits, in FAILURE_BUCKETS order (empty = clean).

    Judge buckets are only tagged when a plan exists — without one the judge
    zeros are downstream noise of the mechanical failure, not a second finding.
    """
    buckets: list[str] = []
    output = row.get("output") or {}
    verdict = row.get("verdict")
    dsl_reason = _meta_reason(row, "dsl_validity")
    exec_reason = _meta_reason(row, "executability")
    did_reason = _meta_reason(row, "did_something")
    sandbox_reason = _meta_reason(row, "sandbox_behavior")
    no_plan = "no resolution_plan" in dsl_reason or "empty ResolutionPlan" in dsl_reason

    if output.get("agent_error"):
        buckets.append("agent_error")
    if verdict == "invalid":
        buckets.append("declared_invalid")
    if verdict == "needs_choice":
        buckets.append("needs_choice")
    if no_plan and verdict == "ok":
        buckets.append("no_plan")
    dsl = _score(row, "dsl_validity")
    if dsl is not None and dsl < FAIL_THRESHOLD and not no_plan:
        buckets.append("invalid_dsl")
    if "requires ctx." in exec_reason or "requires ctx." in did_reason:
        buckets.append("unresolved_choice")
    elif exec_reason and "no executable plan" not in exec_reason:
        buckets.append("runtime_error")
    if "no mechanical ops" in did_reason:
        buckets.append("no_op")
    if sandbox_reason.startswith("execution failed"):
        buckets.append("snippet_crash")
    sandbox = _score(row, "sandbox_behavior")
    if sandbox is not None and sandbox < FAIL_THRESHOLD and not sandbox_reason and not no_plan:
        buckets.append("behavior_mismatch")
    has_plan = bool(output.get("resolution_plan"))
    if has_plan:
        for bucket, metric in _JUDGE_BUCKETS.items():
            score = _score(row, metric)
            if score is not None and score < FAIL_THRESHOLD:
                buckets.append(bucket)
    return sorted(set(buckets), key=FAILURE_BUCKETS.index)


def _run_key(payload: dict[str, Any]) -> str:
    summary = payload.get("summary") or {}
    return f"{payload.get('timestamp', '?')} {summary.get('label') or summary.get('benchmark', '?')}"


def runs_frame(payloads: list[dict[str, Any]]) -> Any:
    """One row per persisted run: config knobs + summary metrics + composite quality.

    ``is_baseline`` marks runs with the production toolbox and default cap —
    the only runs that are apples-to-apples for cross-model comparison.
    """
    import pandas as pd

    records = []
    for payload in payloads:
        config = payload.get("config") or {}
        summary = payload.get("summary") or {}
        enabled = config.get("enabled_tools")
        records.append(
            {
                "run": _run_key(payload),
                "timestamp": payload.get("timestamp"),
                "label": summary.get("label"),
                "benchmark": summary.get("benchmark"),
                "model": summary.get("model"),
                "model_short": short_model(summary.get("model")),
                "max_tool_calls": config.get("max_tool_calls"),
                "enabled_tools": None if enabled is None else "+".join(sorted(enabled)),
                "is_baseline": config.get("max_tool_calls") is None and enabled is None,
                "cases": summary.get("cases"),
                "n_samples": summary.get("n_samples"),
                "quality": quality_score(summary),
                **{m: summary.get(m) for m in ALL_METRICS},
                "executability_ceiling": summary.get("executability_ceiling"),
                "executability_pct_of_ceiling": summary.get("executability_pct_of_ceiling"),
                "did_something_ceiling": summary.get("did_something_ceiling"),
                "did_something_pct_of_ceiling": summary.get("did_something_pct_of_ceiling"),
                "did_something_noop_count": summary.get("did_something_noop_count"),
                "sandbox_na_count": summary.get("sandbox_na_count"),
                "sandbox_interaction_skipped": summary.get("sandbox_interaction_skipped"),
                "invalid_rate": summary.get("invalid_rate"),
                "agent_error_rate": summary.get("agent_error_rate"),
                "mean_tool_calls": summary.get("mean_tool_calls"),
                "mean_cost_usd": summary.get("mean_cost_usd"),
                "total_cost_usd": summary.get("total_cost_usd"),
                "mean_total_tokens": summary.get("mean_total_tokens"),
                "mean_latency_ms": summary.get("mean_latency_ms"),
                "p50_latency_ms": summary.get("p50_latency_ms"),
                "p95_latency_ms": summary.get("p95_latency_ms"),
            }
        )
    return pd.DataFrame(records)


def rows_frame(payloads: list[dict[str, Any]]) -> Any:
    """One row per (run, card, sample): scores, costs, tool usage, failure buckets.

    ``judge_reason`` carries the judge's free-text critique (shared across its
    four metrics), ``mech_reason`` the first deterministic scorer complaint —
    together they are the raw material for failure-pattern aggregation.
    """
    import pandas as pd

    records = []
    for payload in payloads:
        config = payload.get("config") or {}
        summary = payload.get("summary") or {}
        enabled = config.get("enabled_tools")
        run_cols = {
            "run": _run_key(payload),
            "label": summary.get("label"),
            "benchmark": summary.get("benchmark"),
            "model": summary.get("model"),
            "model_short": short_model(summary.get("model")),
            "max_tool_calls": config.get("max_tool_calls"),
            "enabled_tools": None if enabled is None else "+".join(sorted(enabled)),
            "is_baseline": config.get("max_tool_calls") is None and enabled is None,
        }
        for row in payload.get("rows") or []:
            output = row.get("output") or {}
            metrics = row.get("metrics") or {}
            scores = row.get("scores") or {}
            mech_reason = next(
                (
                    reason
                    for reason in (
                        _meta_reason(row, m)
                        for m in ("dsl_validity", "executability", "did_something", "sandbox_behavior")
                    )
                    if reason
                ),
                "",
            )
            records.append(
                {
                    **run_cols,
                    "card_id": row.get("card_id"),
                    "title": row.get("title"),
                    "sample_index": row.get("sample_index"),
                    "verdict": row.get("verdict"),
                    "agent_error": bool(output.get("agent_error")),
                    "tool_calls": metrics.get("tool_calls"),
                    "per_tool": metrics.get("per_tool") or {},
                    "total_tokens": metrics.get("total_tokens"),
                    "latency_ms": row.get("latency_ms"),
                    "cost_usd": row.get("cost_usd"),
                    **{m: scores.get(m) for m in ALL_METRICS},
                    "quality": quality_score(scores),
                    "failure_buckets": failure_buckets(row),
                    "judge_reason": str(((row.get("score_meta") or {}).get("intent_match") or {}).get("reason") or ""),
                    "mech_reason": mech_reason,
                    "comment": output.get("comment"),
                }
            )
    return pd.DataFrame(records)


def bucket_counts(rows: Any, by: str | None = None) -> Any:
    """Failure-bucket frequencies (multi-label), optionally split by a column.

    Returns a DataFrame indexed by bucket (FAILURE_BUCKETS order), either a
    single ``count`` column or one column per ``by`` group.
    """

    exploded = (
        rows[["failure_buckets", *([by] if by else [])]].explode("failure_buckets").dropna(subset=["failure_buckets"])
    )
    if by is None:
        counts = exploded["failure_buckets"].value_counts().rename("count").to_frame()
    else:
        counts = exploded.groupby([by, "failure_buckets"]).size().unstack(by, fill_value=0)
    order = [b for b in FAILURE_BUCKETS if b in counts.index]
    return counts.loc[order]


def card_difficulty(rows: Any) -> Any:
    """Per-card aggregate across every run it appears in, worst first.

    ``models_failed`` lists models with quality < FAIL_THRESHOLD on the card —
    a card failed by *every* model is a card (or canonical) problem; a card
    failed by one model is that model's problem.
    """
    import pandas as pd

    def _agg(group: Any) -> Any:
        failed = group[group["quality"] < FAIL_THRESHOLD]
        buckets = group["failure_buckets"].explode().dropna()
        return pd.Series(
            {
                "title": group["title"].iloc[0],
                "benchmark": group["benchmark"].iloc[0],
                "runs": len(group),
                "mean_quality": group["quality"].mean(),
                "min_quality": group["quality"].min(),
                "fail_rate": len(failed) / len(group),
                "models_failed": sorted(failed["model_short"].unique()),
                "top_bucket": buckets.mode().iloc[0] if len(buckets) else "",
            }
        )

    return rows.groupby("card_id").apply(_agg, include_groups=False).sort_values("mean_quality")


def quality_pivot(rows: Any, columns: str = "model_short") -> Any:
    """card × <columns> matrix of composite quality (mean over samples/runs)."""
    pivot = rows.pivot_table(index=["card_id", "title"], columns=columns, values="quality", aggfunc="mean")
    return pivot.reindex(pivot.mean(axis=1).sort_values().index)


def failing_rows(rows: Any, threshold: float = FAIL_THRESHOLD) -> Any:
    """Rows below the quality threshold — the drill-down / LLM-clustering input."""
    failing = rows[rows["quality"] < threshold]
    return failing.sort_values("quality")
