"""evals.viz — matplotlib comparison charts for eval runs.

Consumes the summary dicts produced by ``EvalRunResult.aggregate()`` (or loaded
from ``evals.store``) and renders the target metrics so multiple runs can be
compared at a glance. Kept out of the notebook so the notebook stays a thin
driver and these are unit-testable.

Palette follows the dataviz skill's validated categorical order (blue, aqua,
yellow, green, violet, red, magenta, orange) — a fixed order, never cycled, so a
run keeps its color across charts. One measure per axis; no dual-axis plots.
"""

from __future__ import annotations

from typing import Any

# dataviz validated categorical hues (light mode), assigned in fixed order.
SERIES_COLORS = ["#2a78d6", "#1baf7a", "#eda100", "#008300", "#4a3aa7", "#e34948", "#e87ba4", "#eb6834"]
_MUTED = "#898781"
_INK = "#0b0b0b"
_GRID = "#e1e0d9"

# The 0..1 quality scorers worth comparing side by side.
QUALITY_METRICS = [
    "intent_match",
    "target_accuracy",
    "persistence_accuracy",
    "magnitude_sign",
    "sandbox_behavior",
    "executability",
    "did_something",
]


def _run_label(summary: dict[str, Any], index: int) -> str:
    label = summary.get("label") or ""
    base = label or f"{summary.get('benchmark', '?')}/{summary.get('model', '?')}"
    return f"{index + 1}. {base}"


def _style_axes(ax: Any) -> None:
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.spines["left"].set_color(_GRID)
    ax.spines["bottom"].set_color(_GRID)
    ax.tick_params(colors=_MUTED, labelsize=9)
    ax.yaxis.grid(True, color=_GRID, linewidth=0.8)
    ax.set_axisbelow(True)


def plot_quality(summaries: list[dict[str, Any]], ax: Any = None) -> Any:
    """Grouped bars: each quality metric (0..1), one bar-cluster per run."""
    import matplotlib.pyplot as plt
    import numpy as np

    if ax is None:
        _, ax = plt.subplots(figsize=(11, 5))
    metrics = [m for m in QUALITY_METRICS if any(m in s and s[m] is not None for s in summaries)]
    x = np.arange(len(metrics))
    n = len(summaries)
    width = 0.8 / max(1, n)
    for i, summary in enumerate(summaries):
        vals = [summary.get(m) or 0.0 for m in metrics]
        ax.bar(x + i * width, vals, width, label=_run_label(summary, i), color=SERIES_COLORS[i % len(SERIES_COLORS)])
    ax.set_xticks(x + width * (n - 1) / 2)
    ax.set_xticklabels(metrics, rotation=30, ha="right")
    ax.set_ylim(0, 1)
    ax.set_ylabel("score (0–1)", color=_MUTED, fontsize=9)
    ax.set_title("Answer quality by metric", color=_INK, fontsize=12, loc="left")
    _style_axes(ax)
    ax.legend(frameon=False, fontsize=8, loc="upper right")
    return ax


def plot_efficiency(summaries: list[dict[str, Any]], axes: Any = None) -> Any:
    """Three single-measure panels: tool calls, cost, p95 latency (one axis each)."""
    import matplotlib.pyplot as plt

    if axes is None:
        _, axes = plt.subplots(1, 3, figsize=(13, 4))
    labels = [_run_label(s, i) for i, s in enumerate(summaries)]
    colors = [SERIES_COLORS[i % len(SERIES_COLORS)] for i in range(len(summaries))]

    panels = [
        ("Mean tool calls / card", [s.get("mean_tool_calls") or 0 for s in summaries], "calls"),
        ("Mean cost / card (USD)", [s.get("mean_cost_usd") or 0 for s in summaries], "USD"),
        ("p95 latency (ms)", [s.get("p95_latency_ms") or 0 for s in summaries], "ms"),
    ]
    for ax, (title, vals, unit) in zip(axes, panels, strict=True):
        ax.bar(range(len(vals)), vals, color=colors, width=0.6)
        ax.set_xticks(range(len(labels)))
        ax.set_xticklabels([str(i + 1) for i in range(len(labels))])
        ax.set_title(title, color=_INK, fontsize=11, loc="left")
        ax.set_ylabel(unit, color=_MUTED, fontsize=9)
        _style_axes(ax)
    return axes


def plot_tool_usage(summaries: list[dict[str, Any]], ax: Any = None) -> Any:
    """Stacked bars: per-tool call totals, one stacked bar per run.

    Shows which tools actually earn their place — a tool never called across a
    whole benchmark is a candidate to drop.
    """
    import matplotlib.pyplot as plt
    import numpy as np

    if ax is None:
        _, ax = plt.subplots(figsize=(10, 5))
    tool_names = sorted({name for s in summaries for name in (s.get("per_tool_calls") or {})})
    if not tool_names:
        ax.text(0.5, 0.5, "no tool calls recorded", ha="center", va="center", color=_MUTED)
        ax.axis("off")
        return ax
    x = np.arange(len(summaries))
    bottom = np.zeros(len(summaries))
    for t, tool in enumerate(tool_names):
        vals = np.array([float((s.get("per_tool_calls") or {}).get(tool, 0)) for s in summaries])
        ax.bar(x, vals, bottom=bottom, label=tool, color=SERIES_COLORS[t % len(SERIES_COLORS)], width=0.6)
        bottom += vals
    ax.set_xticks(x)
    ax.set_xticklabels([_run_label(s, i) for i, s in enumerate(summaries)], rotation=20, ha="right", fontsize=8)
    ax.set_ylabel("total tool calls", color=_MUTED, fontsize=9)
    ax.set_title("Tool usage breakdown", color=_INK, fontsize=12, loc="left")
    _style_axes(ax)
    ax.legend(frameon=False, fontsize=8, ncol=2)
    return ax


def plot_cost_vs_quality(summaries: list[dict[str, Any]], quality_key: str = "intent_match", ax: Any = None) -> Any:
    """Scatter: mean cost (x) vs a quality metric (y). Cheaper-and-better is down-left→up-left."""
    import matplotlib.pyplot as plt

    if ax is None:
        _, ax = plt.subplots(figsize=(7, 6))
    for i, s in enumerate(summaries):
        cost = s.get("mean_cost_usd")
        quality = s.get(quality_key)
        if cost is None or quality is None:
            continue
        color = SERIES_COLORS[i % len(SERIES_COLORS)]
        ax.scatter(cost, quality, s=120, color=color, zorder=3, edgecolor="#fcfcfb", linewidth=1.5)
        ax.annotate(str(i + 1), (cost, quality), color=_INK, fontsize=9, xytext=(6, 4), textcoords="offset points")
    ax.set_xlabel("mean cost / card (USD)", color=_MUTED, fontsize=9)
    ax.set_ylabel(quality_key, color=_MUTED, fontsize=9)
    ax.set_ylim(0, 1)
    ax.set_title(f"Cost vs {quality_key}", color=_INK, fontsize=12, loc="left")
    _style_axes(ax)
    return ax


def summary_table(summaries: list[dict[str, Any]]) -> Any:
    """A pandas DataFrame of the headline metrics per run (one row per run)."""
    import pandas as pd

    keys = [
        "label",
        "benchmark",
        "model",
        "cases",
        "n_samples",
        *QUALITY_METRICS,
        "mean_tool_calls",
        "mean_cost_usd",
        "total_cost_usd",
        "p50_latency_ms",
        "p95_latency_ms",
        "invalid_rate",
    ]
    rows = [{k: s.get(k) for k in keys} for s in summaries]
    return pd.DataFrame(rows)


def worst_cards(run_payload: dict[str, Any], metric: str = "executability", n: int = 10) -> Any:
    """DataFrame of the lowest-scoring cards for drill-down (with judge reason if present)."""
    import pandas as pd

    rows = []
    for r in run_payload.get("rows", []):
        meta = r.get("score_meta") or {}
        rows.append(
            {
                "card_id": r.get("card_id"),
                "title": r.get("title"),
                "verdict": r.get("verdict"),
                metric: (r.get("scores") or {}).get(metric),
                "reason": meta.get(metric, {}).get("reason") or meta.get("intent_match", {}).get("reason"),
            }
        )
    df = pd.DataFrame(rows)
    if metric in df.columns:
        df = df.sort_values(metric, na_position="first")
    return df.head(n)
