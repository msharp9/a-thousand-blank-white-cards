"""agent.stage_runner — generic machinery for ONE bounded ReAct stage.

Extracted from :mod:`agent.runtime` (pure refactor, bead 47b.3) so the
multi-agent pipeline can run the same battle-tested loop per stage with
different output contracts. One stage = build a tool-calling agent, stream it
on a worker thread under a wall-clock timeout, force one tools-disabled
final-answer call on recursion-cap or timeout, and parse the final message
into a caller-chosen pydantic ``output_model``.

:func:`run_stage` never hangs and never raises: it returns ``None`` on total
failure so callers decide their own fallbacks (see ``agent.runtime`` for the
InterpretResult-specific ones). The ``parse``/``on_failure`` hooks let
:func:`agent.runtime.run_agent` re-express its exact historical behavior
through this single copy of the machinery.

Layering: this module may import ``engine``, ``models``, ``config`` and
``logging_config`` but NEVER ``board``.
"""

from __future__ import annotations

import contextvars
import json
import logging
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FuturesTimeoutError
from typing import Any, TypeVar

from langchain.agents import create_agent
from langchain_core.messages import HumanMessage, SystemMessage
from langgraph.errors import GraphRecursionError
from pydantic import BaseModel

from agent.llm import get_chat_model
from models.effects import RegisterHookOp, SnippetStep

logger = logging.getLogger(__name__)

M = TypeVar("M", bound=BaseModel)

FORCED_FINAL_INSTRUCTION = (
    "Budget exhausted — output your final JSON interpretation NOW using what you "
    "already know. Do not call any tools. Respond with ONLY the JSON object "
    "matching the contract."
)

REPAIR_INSTRUCTION = (
    "Your proposed effect failed sandbox validation or dry-run. Return one corrected final JSON object now. "
    "Do not call tools and do not explain the correction outside the JSON."
)


def _extract_final_text(result: dict[str, Any]) -> str:
    """Pull the last AIMessage text content out of an agent invoke result."""
    messages = result.get("messages") or []
    for msg in reversed(messages):
        content = getattr(msg, "content", None)
        # Only AI/assistant messages carry the final answer; tool messages have a
        # `.tool_call_id`, human messages a `.type == "human"`.
        if getattr(msg, "type", None) == "ai" and content:
            if isinstance(content, str):
                return content
            # Content can be a list of blocks (rare); join text pieces.
            parts = [b.get("text", "") if isinstance(b, dict) else str(b) for b in content]
            return "".join(parts)
    return ""


def _fields_candidate(output_model: type[BaseModel]) -> Callable[[Any], bool]:
    """Predicate: is this JSON payload shaped like ``output_model``?

    A candidate is a dict carrying at least one of the model's field names, so
    an unrelated embedded object can't masquerade as the stage's answer.
    """
    keys = tuple(output_model.model_fields)

    def _is_shaped(payload: Any) -> bool:
        return isinstance(payload, dict) and any(key in payload for key in keys)

    return _is_shaped


def _extract_json_object(text: str, is_candidate: Callable[[Any], bool]) -> Any:
    """Parse the first candidate JSON object in ``text``, tolerating prose and fences.

    Models sometimes wrap the answer JSON in commentary or a ```json fence;
    scanning forward with ``raw_decode`` recovers the object wherever it starts
    and ignores anything after it. Embedded candidates must satisfy
    ``is_candidate`` so an inner op object can't masquerade as a result. Raises
    ``json.JSONDecodeError`` when no suitable object exists.
    """
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        pass
    else:
        if is_candidate(parsed):
            return parsed

    decoder = json.JSONDecoder()
    idx = text.find("{")
    while idx != -1:
        try:
            payload, _ = decoder.raw_decode(text, idx)
        except json.JSONDecodeError:
            pass
        else:
            if is_candidate(payload):
                return payload
        idx = text.find("{", idx + 1)
    raise json.JSONDecodeError("no candidate object", text, 0)


def _parse_stage_output(result: dict[str, Any], output_model: type[M]) -> M | None:
    """Default parse: the agent's output as an ``output_model``, or None.

    Preference order:
      1. A ``structured_response`` (when a future version wires response_format).
      2. The last AIMessage parsed as a JSON object shaped like ``output_model``.

    Returns None (never raises) when nothing parseable or valid was produced.
    """
    structured = result.get("structured_response")
    if isinstance(structured, output_model):
        return structured
    if isinstance(structured, dict):
        try:
            return output_model.model_validate(structured)
        except Exception:  # noqa: BLE001 — malformed structured output degrades gracefully
            logger.warning("stage structured_response failed validation; falling back")

    text = _extract_final_text(result).strip()
    if not text:
        return None
    try:
        payload = _extract_json_object(text, _fields_candidate(output_model))
    except json.JSONDecodeError:
        logger.warning("stage final message was not valid JSON")
        return None
    try:
        return output_model.model_validate(payload)
    except Exception:  # noqa: BLE001 — schema mismatch degrades gracefully
        logger.warning("stage final message JSON did not match %s", output_model.__name__)
        return None


def _sanitize_forced_messages(messages: list[Any]) -> list[Any]:
    """Drop a trailing AIMessage with unresolved tool_calls.

    The graph can be interrupted (recursion cap) right after the model asks for
    a tool call but before the tool ran, leaving that call unanswered. Sending
    it onward would break providers that require every tool_call to be
    followed by a matching tool result.
    """
    if messages and getattr(messages[-1], "tool_calls", None):
        return messages[:-1]
    return messages


def _user_text(user_content: str | list[dict[str, Any]]) -> str:
    """The plain text of an opening user message (multimodal blocks stripped)."""
    if isinstance(user_content, str):
        return user_content
    return "".join(b.get("text", "") for b in user_content if isinstance(b, dict) and b.get("type") == "text")


def _build_forced_messages(
    system_prompt: str,
    user_content: str | list[dict[str, Any]],
    progress: list[dict[str, Any]],
) -> list[Any]:
    """Assemble the message list for the tools-disabled forced-final call.

    Prefers the conversation accumulated while streaming the graph (captured in
    ``progress``, see :func:`run_stage`) so the forced call can use partial tool
    results. Degrades to a fresh system+user prompt (the bare-chat-model v1
    path) when nothing was captured.
    """
    if progress:
        messages = _sanitize_forced_messages(list(progress[-1].get("messages") or []))
        if messages:
            # create_agent injects the system prompt at model-call time; it is
            # never written into state["messages"], so it must be re-added here
            # or the forced call loses the persona and the output contract.
            if not any(getattr(m, "type", None) == "system" for m in messages):
                messages = [SystemMessage(content=system_prompt), *messages]
            return [*messages, HumanMessage(content=FORCED_FINAL_INSTRUCTION)]

    return [
        SystemMessage(content=system_prompt),
        HumanMessage(content=_user_text(user_content)),
        HumanMessage(content=FORCED_FINAL_INSTRUCTION),
    ]


def _call_model(chat_model: Any, messages: list[Any], timeout: float, label: str) -> Any | None:
    """Invoke ``chat_model`` once under a wall-clock timeout; None on timeout/error."""
    # No context manager: __exit__ would call shutdown(wait=True) and block on
    # the still-running invoke, defeating the timeout below. shutdown(wait=False)
    # lets us return immediately and abandon the hung thread.
    pool = ThreadPoolExecutor(max_workers=1)
    try:
        # copy_context: the worker thread must see the caller's contextvars
        # (e.g. the eval runner's LangSmith tracing suppression).
        future = pool.submit(contextvars.copy_context().run, chat_model.invoke, messages)
        try:
            return future.result(timeout=timeout)
        except FuturesTimeoutError:
            logger.warning("%s timed out after %.1fs", label, timeout)
            future.cancel()
            return None
        except Exception:  # noqa: BLE001 — the extra call is best-effort
            logger.exception("%s failed", label)
            return None
    finally:
        pool.shutdown(wait=False, cancel_futures=True)


def _forced_final_result(
    chat_model: Any,
    system_prompt: str,
    user_content: str | list[dict[str, Any]],
    progress: list[dict[str, Any]],
    timeout: float,
    parse: Callable[[dict[str, Any]], M | None],
) -> M | None:
    """Make ONE tools-disabled LLM call to force a final answer out of a budget-exhausted agent.

    Returns None (never raises) when the forced call itself times out or errors,
    so the caller can degrade to its own fallback.
    """
    messages = _build_forced_messages(system_prompt, user_content, progress)
    response = _call_model(chat_model, messages, timeout, "forced final-answer call")
    if response is None:
        return None
    return parse({"messages": [response]})


def _effect_validation_error(
    result: Any,
    state: Any | None,
    actor_id: str | None,
    card_id: str | None,
) -> str | None:
    if result.verdict != "ok":
        return None
    plan = result.to_plan()
    if not plan.steps:
        return None

    from engine.sandbox.validate import validate_snippet

    codes = [step.code for step in plan.steps if isinstance(step, SnippetStep)]
    codes.extend(op.code for op in plan.operations() if isinstance(op, RegisterHookOp))
    for code in codes:
        validation = validate_snippet(code)
        if not validation.ok:
            return validation.error

    if state is None or not codes or plan.requires_choice:
        return None

    from agent.tools.dry_run_effect import dry_run_resolution_plan

    report = dry_run_resolution_plan(state, plan, actor_id, card_id)
    return None if report["ok"] else str(report["error"])


def _repair_effect(
    chat_model: Any,
    system_prompt: str,
    title: str,
    result: M,
    error: str,
    timeout: float,
    parse: Callable[[dict[str, Any]], M | None],
) -> M | None:
    messages = [
        SystemMessage(content=system_prompt),
        HumanMessage(
            content=(
                f"Card: {title}\nInvalid result: {result.model_dump_json()}\n"
                f"Validation error: {error}\n{REPAIR_INSTRUCTION}"
            )
        ),
    ]
    response = _call_model(chat_model, messages, timeout, "effect repair call")
    if response is None:
        return None
    return parse({"messages": [response]})


def _validate_or_repair_effect(
    result: M,
    chat_model: Any,
    system_prompt: str,
    title: str,
    state: Any | None,
    actor_id: str | None,
    card_id: str | None,
    timeout: float,
    parse: Callable[[dict[str, Any]], M | None],
    *,
    precomputed_error: str | None = None,
) -> M:
    """Validate ``result``'s effect; on failure make ONE repair call, then strip.

    ``precomputed_error`` lets a caller that already ran
    :func:`_effect_validation_error` skip the duplicate validation/dry-run pass
    (None means validate here); a repaired candidate is always re-validated.
    """
    error = precomputed_error
    if error is None:
        error = _effect_validation_error(result, state, actor_id, card_id)
    if error is None:
        return result
    logger.warning("agent effect failed validation: %s", error)
    repaired = _repair_effect(chat_model, system_prompt, title, result, error, timeout, parse)
    if repaired is not None and _effect_validation_error(repaired, state, actor_id, card_id) is None:
        return repaired
    return result.model_copy(update={"plan": None, "program": None, "snippet": None, "verdict": "invalid"})


def run_stage(
    system_prompt: str,
    user_content: str | list[dict[str, Any]],
    tools: list[Any] | None,
    model: Any | None,
    output_model: type[M],
    *,
    timeout: float,
    max_steps: int,
    forced_call_timeout: float = 30.0,
    config: dict[str, Any] | None = None,
    agent: Any | None = None,
    parse: Callable[[dict[str, Any]], M | None] | None = None,
    on_failure: Callable[[str, BaseException | None], M | None] | None = None,
) -> M | None:
    """Run one bounded ReAct stage. Never hangs, never raises.

    Args:
        system_prompt: The stage's system prompt (persona + output contract).
        user_content: Opening user message — plain text or multimodal blocks.
        tools: Tools to bind AS-IS (an empty/None list binds no tools).
        model: Chat model; defaults to :func:`agent.llm.get_chat_model`.
        output_model: Pydantic model the final answer is parsed into.
        timeout: Wall-clock ceiling in seconds for the streamed agent run.
        max_steps: LangGraph recursion/step cap.
        forced_call_timeout: Wall-clock ceiling for the forced tools-disabled
            final-answer call made on recursion-cap/timeout.
        config: Extra keys merged into the LangGraph run config (e.g.
            ``{"callbacks": [...]}``). ``recursion_limit`` is always set from
            ``max_steps`` and cannot be overridden here.
        agent: A prebuilt compiled agent; when given, ``tools`` are ignored and
            ``model`` is used only for the forced/repair calls.
        parse: Override for turning a graph result into an ``output_model``
            instance (must not raise). Defaults to :func:`_parse_stage_output`.
        on_failure: Callback deciding the return value for a total failure
            (``kind`` in ``"construction"``/``"timeout"``/``"recursion"``/
            ``"error"``; invoked inside the corresponding except block so
            ``exc_info`` logging works). Defaults to returning None.

    Returns:
        The parsed ``output_model`` instance, or whatever ``parse``/``on_failure``
        return for the corresponding path — None on total failure by default.
    """
    if parse is None:

        def _default_parse(result: dict[str, Any]) -> M | None:
            return _parse_stage_output(result, output_model)

        parse = _default_parse

    def _fail(kind: str, exc: BaseException | None) -> M | None:
        return on_failure(kind, exc) if on_failure is not None else None

    try:
        chat_model = model if model is not None else get_chat_model()
        if agent is None:
            agent = create_agent(model=chat_model, tools=list(tools) if tools else [], system_prompt=system_prompt)
    except Exception as exc:  # noqa: BLE001 — model/agent construction must never escape
        logger.exception("stage agent construction failed")
        return _fail("construction", exc)

    inputs = {"messages": [("user", user_content)]}
    # recursion_limit is authoritative and set last so a passed-in config can add
    # callbacks/tags/metadata but never weaken the step cap.
    run_config = {**(config or {}), "recursion_limit": max_steps}

    # progress accumulates every intermediate graph state so that, if the graph
    # is interrupted by the recursion cap or the timeout below, we still have the
    # conversation-so-far to hand to the forced final-answer call.
    progress: list[dict[str, Any]] = []

    def _stream_agent() -> dict[str, Any] | None:
        for chunk in agent.stream(inputs, run_config, stream_mode="values"):
            progress.append(chunk)
        return progress[-1] if progress else None

    # Run the (synchronous) streaming loop on a worker thread so we can enforce a
    # hard wall-clock timeout even if the underlying call blocks on the network.
    # No context manager: __exit__ would call shutdown(wait=True) and block on
    # the still-running stream, defeating the timeout below. shutdown(wait=False)
    # lets us return immediately and abandon the hung thread.
    pool = ThreadPoolExecutor(max_workers=1)
    try:
        # copy_context: the worker thread must see the caller's contextvars
        # (e.g. the eval runner's LangSmith tracing suppression).
        future = pool.submit(contextvars.copy_context().run, _stream_agent)
        try:
            result = future.result(timeout=timeout)
        except FuturesTimeoutError:
            logger.warning("stage timed out after %.1fs; forcing a final answer", timeout)
            future.cancel()
            forced = _forced_final_result(chat_model, system_prompt, user_content, progress, forced_call_timeout, parse)
            if forced is not None:
                return forced
            return _fail("timeout", None)
        except GraphRecursionError:
            logger.warning("stage hit recursion cap (%d); forcing a final answer", max_steps)
            forced = _forced_final_result(chat_model, system_prompt, user_content, progress, forced_call_timeout, parse)
            if forced is not None:
                return forced
            return _fail("recursion", None)
        except Exception as exc:  # noqa: BLE001 — any agent-internal error degrades gracefully
            if on_failure is not None:
                return on_failure("error", exc)
            logger.exception("stage invoke failed")
            return None
    finally:
        pool.shutdown(wait=False, cancel_futures=True)

    return parse(result if result is not None else {})
