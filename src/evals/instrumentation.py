"""evals.instrumentation — capture per-run usage from one run_agent invocation.

A single :class:`UsageCallback` is attached (via ``run_agent(config={"callbacks": [cb]})``)
for the duration of one card interpretation. Attaching at the run-config level
means the handler sees BOTH LLM nodes (token usage) and tool nodes (tool-call
counts) — a model-bound callback would miss tool starts.

Nothing here ever raises into the agent: callbacks that fail must degrade to
missing data, never break the interpretation being measured. Token usage is
best-effort — some gateways omit ``usage_metadata`` — so ``total_tokens`` may be
``None`` while tool counts are still exact.
"""

from __future__ import annotations

import logging
from collections import Counter
from dataclasses import dataclass, field
from typing import Any

from langchain_core.callbacks import BaseCallbackHandler

from config import EVAL_MODEL_PRICES

logger = logging.getLogger("evals.instrumentation")


@dataclass(frozen=True, slots=True)
class RunMetrics:
    """Usage captured across one run_agent call. Token fields are None when the
    provider/gateway reported no usage metadata."""

    tool_calls: int = 0
    per_tool: dict[str, int] = field(default_factory=dict)
    llm_calls: int = 0
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    total_tokens: int | None = None


class UsageCallback(BaseCallbackHandler):
    """Accumulate tool-call counts and token usage for one interpretation.

    Create a fresh instance per card×sample; do not share across runs.
    """

    def __init__(self) -> None:
        self._tool_counter: Counter[str] = Counter()
        self._llm_calls = 0
        self._prompt_tokens = 0
        self._completion_tokens = 0
        self._total_tokens = 0
        self._saw_usage = False

    # --- tool nodes -------------------------------------------------------- #
    def on_tool_start(self, serialized: dict[str, Any] | None, input_str: str, **kwargs: Any) -> None:
        try:
            name = (serialized or {}).get("name") or kwargs.get("name") or "unknown"
            self._tool_counter[str(name)] += 1
        except Exception:  # noqa: BLE001 — instrumentation must never break a run
            logger.debug("on_tool_start bookkeeping failed", exc_info=True)

    # --- LLM nodes --------------------------------------------------------- #
    def on_llm_end(self, response: Any, **kwargs: Any) -> None:
        try:
            self._llm_calls += 1
            usage = _extract_usage(response)
            if usage is None:
                return
            self._saw_usage = True
            self._prompt_tokens += int(usage.get("input_tokens") or usage.get("prompt_tokens") or 0)
            self._completion_tokens += int(usage.get("output_tokens") or usage.get("completion_tokens") or 0)
            self._total_tokens += int(usage.get("total_tokens") or 0)
        except Exception:  # noqa: BLE001
            logger.debug("on_llm_end bookkeeping failed", exc_info=True)

    def snapshot(self) -> RunMetrics:
        """Freeze the accumulated counts into a RunMetrics."""
        if not self._saw_usage:
            logger.debug("no LLM usage metadata seen; token counts reported as None")
        return RunMetrics(
            tool_calls=sum(self._tool_counter.values()),
            per_tool=dict(self._tool_counter),
            llm_calls=self._llm_calls,
            prompt_tokens=self._prompt_tokens if self._saw_usage else None,
            completion_tokens=self._completion_tokens if self._saw_usage else None,
            total_tokens=(self._total_tokens or (self._prompt_tokens + self._completion_tokens))
            if self._saw_usage
            else None,
        )


def _extract_usage(response: Any) -> dict[str, Any] | None:
    """Pull a usage dict out of an LLMResult across the shapes providers use.

    Checks, in order: per-generation ``message.usage_metadata`` (the modern
    LangChain field), then ``llm_output['token_usage']`` (the classic
    OpenAI-style location). Returns None when neither is present.
    """
    # Modern: generations[i][j].message.usage_metadata
    generations = getattr(response, "generations", None)
    if generations:
        for batch in generations:
            for gen in batch:
                message = getattr(gen, "message", None)
                usage = getattr(message, "usage_metadata", None)
                if usage:
                    return dict(usage)
    llm_output = getattr(response, "llm_output", None)
    if isinstance(llm_output, dict):
        token_usage = llm_output.get("token_usage") or llm_output.get("usage")
        if token_usage:
            return dict(token_usage)
    return None


def cost_usd(metrics: RunMetrics, model: str, prices: dict[str, dict[str, float]] | None = None) -> float | None:
    """USD cost of one run from its token counts and a per-model price table.

    Returns None when token usage was unavailable (so cost can't be derived).
    ``prices`` defaults to :data:`config.EVAL_MODEL_PRICES`; unknown models fall
    back to the table's ``"default"`` entry (or 0.0 if absent).
    """
    if metrics.prompt_tokens is None and metrics.completion_tokens is None:
        return None
    table = prices if prices is not None else EVAL_MODEL_PRICES
    # bifrost serves models both bare and under a "bedrock/" prefix — accept either.
    rate = (
        table.get(model)
        or table.get(model.removeprefix("bedrock/"))
        or table.get("default")
        or {"input": 0.0, "output": 0.0}
    )
    prompt = metrics.prompt_tokens or 0
    completion = metrics.completion_tokens or 0
    return (prompt * rate["input"] + completion * rate["output"]) / 1_000_000
