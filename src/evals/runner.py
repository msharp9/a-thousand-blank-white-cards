"""evals.runner — production-faithful eval runner for the interpretation agent.

Unlike the old ``harness.make_task`` (which called ``run_agent`` with only
title+description), this runner reproduces the real-play path: a live parity
``GameState`` + actor + author, the full production toolbox (optionally
filtered), the honored caps, and vision when enabled. Every call is
instrumented for tool-call counts, token usage, cost, and latency.

The generic ``eval_core.run_eval`` can't carry per-sample usage metrics or
N-sampling, so this module owns its own loop and result types while reusing the
``Scorer`` objects from ``evals.scorers`` and ``normalise_agent_output``.

Determinism note: ``Date.now``-style calls stay out of here — the caller stamps
a timestamp when persisting (see ``evals.store``).
"""

from __future__ import annotations

import json
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import nullcontext
from dataclasses import dataclass
from pathlib import Path
from statistics import fmean
from time import perf_counter
from typing import Any

from langsmith.run_helpers import tracing_context

from config import EVAL_BENCHMARKS
from evals.instrumentation import RunMetrics, UsageCallback, cost_usd
from evals.paths import find_repo_root
from evals.scorers import ALL_SCORERS, DETERMINISTIC_SCORERS, reset_run_caches

logger = logging.getLogger("evals.runner")


@dataclass(frozen=True, slots=True)
class EvalConfig:
    """One eval configuration — the knobs the notebook exposes.

    ``enabled_tools`` None means the production default toolbox; an explicit set
    filters the assembled tools by ``tool.name`` (empty set = no tools).
    ``benchmark`` keys into :data:`config.EVAL_BENCHMARKS`. ``sample_size`` caps
    cards per benchmark (None = all); ``n_samples`` repeats each card to measure
    stochastic consistency. ``concurrency`` > 1 runs that many cards in parallel
    worker threads — each card is fully isolated (own state, callback, agent),
    matching how production rooms already invoke run_agent concurrently.
    """

    benchmark: str = "eval"
    model_name: str | None = None
    enabled_tools: frozenset[str] | None = None
    allow_persistent_tools: bool = False
    max_tool_calls: int | None = None
    timeout: float | None = None
    vision: bool = False
    n_samples: int = 1
    sample_size: int | None = None
    concurrency: int = 1
    use_judge: bool = True
    tracing: bool = False
    prices: dict[str, dict[str, float]] | None = None
    label: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "benchmark": self.benchmark,
            "model_name": self.model_name,
            "enabled_tools": sorted(self.enabled_tools) if self.enabled_tools is not None else None,
            "allow_persistent_tools": self.allow_persistent_tools,
            "max_tool_calls": self.max_tool_calls,
            "timeout": self.timeout,
            "vision": self.vision,
            "n_samples": self.n_samples,
            "sample_size": self.sample_size,
            "concurrency": self.concurrency,
            "use_judge": self.use_judge,
            "tracing": self.tracing,
            "label": self.label,
        }


@dataclass(frozen=True, slots=True)
class CardResult:
    """One card × one sample: the agent output, its usage, and scores."""

    card_id: str
    title: str
    sample_index: int
    verdict: str
    output: dict[str, Any]
    metrics: RunMetrics
    latency_ms: float
    cost_usd: float | None
    scores: dict[str, float]
    score_meta: dict[str, dict[str, Any]]


@dataclass(frozen=True, slots=True)
class EvalRunResult:
    """All rows from one benchmark run plus its config and scorer names."""

    config: EvalConfig
    scorer_names: tuple[str, ...]
    rows: tuple[CardResult, ...]
    timestamp: str = ""

    def aggregate(self) -> dict[str, Any]:
        return _aggregate(self)


# --------------------------------------------------------------------------- #
# Dataset loading
# --------------------------------------------------------------------------- #
def load_cards(benchmark: str, sample_size: int | None = None) -> list[dict[str, Any]]:
    """Load one benchmark's cards, normalising the canonical key to
    ``human_canonical`` so scorers and expected-labels are uniform.

    ``sample_size`` truncates deterministically (first N, dataset order); the
    truncation is logged so a bounded run never silently reads as full coverage.
    """
    if benchmark not in EVAL_BENCHMARKS:
        raise ValueError(f"Unknown benchmark {benchmark!r}; choose from {sorted(EVAL_BENCHMARKS)}.")
    spec = EVAL_BENCHMARKS[benchmark]
    path = find_repo_root(Path(__file__)) / str(spec["path"])
    cards: list[dict[str, Any]] = json.loads(path.read_text(encoding="utf-8"))
    key = str(spec["canonical_key"])
    if key != "human_canonical":
        for card in cards:
            card["human_canonical"] = card.get(key)
    if sample_size is not None and sample_size < len(cards):
        logger.warning("benchmark %s: sampling first %d of %d cards", benchmark, sample_size, len(cards))
        cards = cards[:sample_size]
    return cards


# --------------------------------------------------------------------------- #
# Tool assembly (with by-name filtering)
# --------------------------------------------------------------------------- #
def _build_tools(config: EvalConfig, state: Any, actor_id: str, creator_id: str, card_id: str) -> list[Any]:
    """Assemble the production toolbox for this run, filtered by name.

    Reuses ``run_agent``'s own assembler so the eval sees exactly the tools
    production would, then filters to ``enabled_tools`` when set. Returning the
    full list unfiltered (enabled_tools=None) matches production defaults.
    """
    from agent.runtime import _assemble_tools

    tools = _assemble_tools(
        state,
        actor_id,
        creator_id,
        None,
        card_id,
        allow_persistent_tools=config.allow_persistent_tools,
    )
    if config.enabled_tools is None:
        return tools
    return [t for t in tools if getattr(t, "name", None) in config.enabled_tools]


def available_tool_names(allow_persistent_tools: bool = True) -> list[str]:
    """Names of every tool the parity state would bind — for the notebook's toggle UI."""
    from evals.game_fixtures import EVAL_ACTOR_ID, EVAL_CARD_ID, EVAL_CREATOR_ID, build_eval_state

    cfg = EvalConfig(allow_persistent_tools=allow_persistent_tools)
    tools = _build_tools(cfg, build_eval_state(), EVAL_ACTOR_ID, EVAL_CREATOR_ID, EVAL_CARD_ID)
    return sorted(getattr(t, "name", "unknown") for t in tools)


# --------------------------------------------------------------------------- #
# Run one card
# --------------------------------------------------------------------------- #
def _run_one(config: EvalConfig, card: dict[str, Any], sample_index: int, scorers: list[Any]) -> CardResult:
    from agent.llm import get_chat_model
    from agent.runtime import run_agent
    from evals.game_fixtures import EVAL_ACTOR_ID, EVAL_CARD_ID, EVAL_CREATOR_ID, build_eval_state
    from evals.harness import normalise_agent_output

    title = str(card.get("title", ""))
    description = str(card.get("description", ""))
    alt_text = card.get("alt_text")
    state = build_eval_state(title=title, description=description, alt_text=alt_text)
    tools = _build_tools(config, state, EVAL_ACTOR_ID, EVAL_CREATOR_ID, EVAL_CARD_ID)
    model = get_chat_model(config.model_name) if config.model_name else None
    callback = UsageCallback()

    trace_ctx = nullcontext() if config.tracing else tracing_context(enabled=False)
    with trace_ctx:
        t0 = perf_counter()
        result = run_agent(
            title,
            description,
            state,
            EVAL_ACTOR_ID,
            creator_id=EVAL_CREATOR_ID,
            card_id=EVAL_CARD_ID,
            card_art=alt_text if config.vision else None,
            tools=tools,
            model=model,
            max_tool_calls=config.max_tool_calls,
            timeout=config.timeout,
            allow_persistent_tools=config.allow_persistent_tools,
            config={"callbacks": [callback]},
        )
        latency_ms = (perf_counter() - t0) * 1_000
        metrics = callback.snapshot()

        output = normalise_agent_output(result)
        expected = card.get("human_canonical") or {}
        from evals.eval_core import EvalItem, ScorerContext

        item = EvalItem(id=str(card.get("id", "card")), input=card, expected=expected)
        ctx = ScorerContext(item=item, output=output)
        scores: dict[str, float] = {}
        score_meta: dict[str, dict[str, Any]] = {}
        for scorer in scorers:
            try:
                score = scorer.evaluate(ctx)
                scores[scorer.name] = score.score
                score_meta[scorer.name] = dict(score.metadata)
            except Exception as exc:  # noqa: BLE001 — a scorer failure shouldn't kill the run
                logger.warning("scorer %s failed on %s: %s", scorer.name, item.id, exc)
                score_meta[scorer.name] = {"error": str(exc)}

    return CardResult(
        card_id=item.id,
        title=title,
        sample_index=sample_index,
        verdict=getattr(result, "verdict", "invalid"),
        output=output,
        metrics=metrics,
        latency_ms=latency_ms,
        cost_usd=cost_usd(metrics, config.model_name or _resolved_model_name(), config.prices),
        scores=scores,
        score_meta=score_meta,
    )


def _resolved_model_name() -> str:
    from config import get_settings

    return get_settings().chat_model


def run_benchmark(config: EvalConfig, *, timestamp: str = "", progress: bool = True) -> EvalRunResult:
    """Run one benchmark end-to-end and return the collected result.

    Rows come back in dataset order regardless of ``concurrency``, so runs over
    the same benchmark stay directly comparable.
    """
    reset_run_caches()
    scorers = ALL_SCORERS if config.use_judge else DETERMINISTIC_SCORERS
    cards = load_cards(config.benchmark, config.sample_size)
    work = [(card, sample_index) for card in cards for sample_index in range(config.n_samples)]

    def _report(done: int, card: dict[str, Any], sample_index: int) -> None:
        if progress:
            logger.info("[%d/%d] %s (sample %d)", done, len(work), card.get("title", "?"), sample_index)

    if config.concurrency <= 1:
        rows = []
        for done, (card, sample_index) in enumerate(work, start=1):
            rows.append(_run_one(config, card, sample_index, scorers))
            _report(done, card, sample_index)
    else:
        rows = [None] * len(work)
        with ThreadPoolExecutor(max_workers=config.concurrency) as pool:
            futures = {
                pool.submit(_run_one, config, card, sample_index, scorers): i
                for i, (card, sample_index) in enumerate(work)
            }
            for done, future in enumerate(as_completed(futures), start=1):
                i = futures[future]
                rows[i] = future.result()
                _report(done, *work[i])

    return EvalRunResult(
        config=config,
        scorer_names=tuple(s.name for s in scorers),
        rows=tuple(rows),
        timestamp=timestamp,
    )


# --------------------------------------------------------------------------- #
# Aggregation
# --------------------------------------------------------------------------- #
def _percentile(values: list[float], pct: float) -> float:
    """Nearest-rank percentile (pct in 0..100). Empty -> 0.0."""
    if not values:
        return 0.0
    ordered = sorted(values)
    rank = max(0, min(len(ordered) - 1, round(pct / 100 * (len(ordered) - 1))))
    return ordered[rank]


def _aggregate(run: EvalRunResult) -> dict[str, Any]:
    rows = run.rows
    if not rows:
        return {"cases": 0}

    latencies = [r.latency_ms for r in rows]
    tool_calls = [r.metrics.tool_calls for r in rows]
    costs = [r.cost_usd for r in rows if r.cost_usd is not None]
    total_tokens = [r.metrics.total_tokens for r in rows if r.metrics.total_tokens is not None]

    per_tool: dict[str, int] = {}
    for r in rows:
        for name, count in r.metrics.per_tool.items():
            per_tool[name] = per_tool.get(name, 0) + count

    verdict_counts: dict[str, int] = {}
    for r in rows:
        verdict_counts[r.verdict] = verdict_counts.get(r.verdict, 0) + 1

    summary: dict[str, Any] = {
        "benchmark": run.config.benchmark,
        "label": run.config.label,
        "model": run.config.model_name or _resolved_model_name(),
        "cases": len(rows),
        "unique_cards": len({r.card_id for r in rows}),
        "n_samples": run.config.n_samples,
        "mean_tool_calls": fmean(tool_calls),
        "per_tool_calls": per_tool,
        "mean_latency_ms": fmean(latencies),
        "p50_latency_ms": _percentile(latencies, 50),
        "p95_latency_ms": _percentile(latencies, 95),
        "total_cost_usd": sum(costs) if costs else None,
        "mean_cost_usd": fmean(costs) if costs else None,
        "mean_total_tokens": fmean(total_tokens) if total_tokens else None,
        "verdict_counts": verdict_counts,
        "invalid_rate": verdict_counts.get("invalid", 0) / len(rows),
        "agent_error_rate": sum(1 for r in rows if r.output.get("agent_error")) / len(rows),
    }
    for name in run.scorer_names:
        vals = [r.scores[name] for r in rows if name in r.scores]
        summary[name] = fmean(vals) if vals else None

    _add_ceilings(run, summary)

    # Consistency: only meaningful when a card is sampled more than once.
    if run.config.n_samples > 1:
        summary["consistency"] = _consistency(run)
    return summary


def _add_ceilings(run: EvalRunResult, summary: dict[str, Any]) -> None:
    """Annotate the summary with each deterministic metric's achievable ceiling.

    executability/did_something are capped by card nature (a no-op card can't
    "do something"), so the raw mean is misleading without its ceiling. Computed
    from the actually-run cards' own canonicals; best-effort — a benchmark whose
    labels aren't executable canonicals simply gets no ceiling fields.
    """
    from evals.ceilings import benchmark_ceilings

    try:
        by_id = {str(card.get("id")): card for card in load_cards(run.config.benchmark)}
    except Exception:  # noqa: BLE001 — ceilings are advisory; never break a run's summary
        return
    run_cards = [by_id[cid] for cid in {r.card_id for r in run.rows} if cid in by_id]
    ceilings = benchmark_ceilings(run_cards)
    if not ceilings:
        return
    summary.update(ceilings)
    for metric in ("executability", "did_something"):
        ceiling = ceilings.get(f"{metric}_ceiling")
        actual = summary.get(metric)
        if ceiling and actual is not None:
            summary[f"{metric}_pct_of_ceiling"] = actual / ceiling


def _consistency(run: EvalRunResult) -> dict[str, float]:
    """Mean within-card stdev of the ``overall``-ish signals across samples.

    Lower = more deterministic. Uses executability (deterministic) and
    intent_match (if judged) as the stability probes."""
    from statistics import pstdev

    by_card: dict[str, list[CardResult]] = {}
    for r in run.rows:
        by_card.setdefault(r.card_id, []).append(r)
    out: dict[str, float] = {}
    for probe in ("intent_match", "executability", "did_something"):
        spreads = [
            pstdev([r.scores[probe] for r in group if probe in r.scores])
            for group in by_card.values()
            if len([r for r in group if probe in r.scores]) > 1
        ]
        if spreads:
            out[f"{probe}_stdev"] = fmean(spreads)
    return out
