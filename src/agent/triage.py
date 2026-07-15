"""agent.triage — LLM triage of card effects the engine failed to execute.

When a played card falls back to a mechanical no-op (sandbox crash, invalid
verdict, hook failure, …), this module diagnoses why via a structured-output
LLM call and folds the result into the existing capability-wish telemetry sink
for human review. Everything here is best-effort: ``build_triage_report``
degrades to a deterministic report when the LLM/gateway is unavailable, and
the scheduler runs reports fire-and-forget under a concurrency cap so triage
never blocks or breaks gameplay.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Literal, get_args

from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel, Field

from agent.llm import get_chat_model
from agent.tools.capability_wish import record_capability_wish
from config import get_settings

logger = logging.getLogger("agent.triage")

KIND = Literal["sandbox_failure", "no_op", "invalid_verdict", "hook_failure", "interaction_setup"]
_VALID_KINDS: frozenset[str] = frozenset(get_args(KIND))


@dataclass(frozen=True, slots=True)
class CardFailure:
    """Pure-data snapshot of one failed effect execution.

    Deliberately free of board/agent-runtime imports so any layer can construct
    one. ``run_metrics`` is an already-serialized ``RunMetrics`` dict (see
    evals.instrumentation); this module only reads it, never re-instruments.
    """

    kind: str
    card_title: str
    card_description: str
    card_id: str
    correlation_id: str
    verdict: str | None = None
    comment: str | None = None
    exception: str | None = None
    mechanical_status: str = "fallback"
    fallback_note: str = ""
    state_summary: str = ""
    history_summary: str = ""
    run_metrics: dict | None = None
    langsmith: dict | None = None

    @property
    def dedupe_key(self) -> tuple[str, str]:
        return (self.card_id, self.kind)


class TriageReport(BaseModel):
    """Structured triage verdict for one effect failure."""

    diagnosis: str = Field(description="What went wrong, in 1-2 sentences.")
    root_cause_bucket: KIND
    what_the_card_wanted: str = Field(description="The behavior the card asked for, in plain terms.")
    missing_capability: str = Field(description="Short name of the engine capability that was missing.")
    recommendation: str = Field(description="Concrete engine/capability change that would fix this class of failure.")
    severity: Literal["low", "medium", "high"]
    confidence: float = Field(ge=0, le=1)


_SYSTEM_PROMPT = """\
You are a triage analyst for a card game engine ("1000 Blank White Cards") where players write
free-text cards and an AI agent compiles them into mechanical effects. You are given one card
that the engine could NOT mechanically execute, plus the failure context.

Diagnose why execution failed and recommend a concrete engine or capability change that would
let cards like this one work. Name the specific missing capability, not a vague theme.
Be specific and terse.
"""


def _render_payload(payload: CardFailure) -> str:
    parts = [
        f"FAILURE KIND: {payload.kind}",
        f"CARD TITLE: {payload.card_title}",
        f"CARD DESCRIPTION:\n{payload.card_description}",
        f"AGENT VERDICT: {payload.verdict}",
        f"AGENT COMMENT: {payload.comment}",
        f"EXCEPTION: {payload.exception}",
        f"FALLBACK NOTE: {payload.fallback_note}",
        f"GAME STATE SUMMARY:\n{payload.state_summary}",
        f"RECENT HISTORY:\n{payload.history_summary}",
    ]
    if payload.run_metrics is not None:
        parts.append(f"RUN METRICS: {payload.run_metrics}")
    return "\n\n".join(parts)


def _fallback_report(payload: CardFailure) -> TriageReport:
    """Deterministic report used when the LLM call/parse fails; keeps tests and
    gateway-down operation working."""
    bucket = payload.kind if payload.kind in _VALID_KINDS else "sandbox_failure"
    exception_suffix = f": {payload.exception}" if payload.exception else ""
    return TriageReport(
        diagnosis=f"Effect execution failed ({payload.kind}){exception_suffix}",
        root_cause_bucket=bucket,  # type: ignore[arg-type]
        what_the_card_wanted=payload.card_description or "unknown",
        missing_capability=f"unclassified {payload.kind}{exception_suffix}",
        recommendation="Human review needed: automatic triage was unavailable for this failure.",
        severity="low",
        confidence=0.0,
    )


def build_triage_report(payload: CardFailure, *, model: str | None = None) -> TriageReport:
    """Diagnose one effect failure via a structured-output LLM call.

    Never raises: any LLM/parse error degrades to the deterministic fallback
    report built purely from the payload.
    """
    resolved = model or get_settings().triage_agent_model or None
    try:
        llm = get_chat_model(resolved).with_structured_output(TriageReport)
        response = llm.invoke([SystemMessage(content=_SYSTEM_PROMPT), HumanMessage(content=_render_payload(payload))])
        if not isinstance(response, TriageReport):
            raise TypeError(f"triage agent returned unexpected type: {type(response)}")
        return response
    except Exception:
        logger.debug("LLM triage failed; using deterministic fallback report", exc_info=True)
        return _fallback_report(payload)


async def run_triage(payload: CardFailure, *, model: str | None = None) -> dict:
    """Triage one failure and persist it as a capability wish. Never raises."""
    try:
        report = await asyncio.to_thread(build_triage_report, payload, model=model)
        what_i_wanted = f"[{payload.kind}] {report.what_the_card_wanted} — recommendation: {report.recommendation}"
        record = await asyncio.to_thread(
            record_capability_wish,
            payload.card_title,
            payload.card_description,
            what_i_wanted,
            report.missing_capability,
        )
        logger.info(
            "effect failure triaged (card=%s kind=%s): %s | recommendation: %s",
            payload.card_id,
            payload.kind,
            report.diagnosis,
            report.recommendation,
        )
        return record
    except Exception as exc:
        logger.exception("run_triage failed (card=%s kind=%s)", payload.card_id, payload.kind)
        return {"recorded": False, "error": str(exc)}


class TriageScheduler:
    """Fire-and-forget task scheduler with a concurrency cap.

    The semaphore is loop-bound, so it is created lazily on first use inside a
    running loop (sized to ``triage_agent_max_concurrency``); tests rebind it via
    ``reset_scheduler``.
    """

    def __init__(self) -> None:
        self._tasks: set[asyncio.Task] = set()
        self._semaphore: asyncio.Semaphore | None = None

    def _get_semaphore(self) -> asyncio.Semaphore:
        if self._semaphore is None:
            self._semaphore = asyncio.Semaphore(get_settings().triage_agent_max_concurrency)
        return self._semaphore

    def schedule(self, coro_factory: Callable[[], Awaitable[object]]) -> None:
        """Schedule ``coro_factory`` fire-and-forget; drops (with a warning) when
        no event loop is running."""
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            logger.warning("no running event loop; dropping triage-agent report")
            return
        task = loop.create_task(self._run(coro_factory))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _run(self, coro_factory: Callable[[], Awaitable[object]]) -> None:
        async with self._get_semaphore():
            try:
                await coro_factory()
            except Exception:
                logger.exception("scheduled triage-agent report failed")

    async def drain(self, timeout: float | None = None) -> None:
        """Await pending tasks (up to ``timeout``), cancel stragglers. Never raises."""
        try:
            pending = set(self._tasks)
            if not pending:
                return
            _, still_pending = await asyncio.wait(pending, timeout=timeout)
            for task in still_pending:
                task.cancel()
        except Exception:
            logger.exception("triage-agent drain failed")


_scheduler = TriageScheduler()


def get_scheduler() -> TriageScheduler:
    return _scheduler


def reset_scheduler() -> None:
    """Rebuild the singleton so its semaphore rebinds to the current test loop."""
    global _scheduler
    _scheduler = TriageScheduler()


def schedule_triage(payload: CardFailure, *, model: str | None = None) -> None:
    """Queue an async triage report for ``payload`` when the triage agent is enabled."""
    if not get_settings().triage_agent_enabled:
        return
    get_scheduler().schedule(lambda: run_triage(payload, model=model))
