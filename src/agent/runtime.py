"""agent.runtime — the NEW single tool-calling agent (skeleton, bead C1).

This replaced the legacy 9-node LangGraph (now removed). It assembles ONE
ReAct/tool-calling agent (LangChain ``create_agent``) with:

- the persona system prompt from :mod:`agent.persona`,
- a bound tool list (EMPTY by default — the seven real tools land in beads C2-C9;
  binding them is a one-liner: pass ``tools=[...]`` to :func:`build_agent`),
- a hard recursion / tool-call cap AND a wall-clock timeout,
- LangSmith tracing wired OFF by default (behind ``Settings.langsmith_tracing``),
- the forward-looking :class:`~agent.contract.InterpretResult` output contract.

Layering: this module may import ``engine``, ``models``, ``config`` and
``logging_config`` but NEVER ``board``. Tools that need live game state (later
beads) take a PASSED-IN snapshot; :func:`run_agent` threads ``state``/``actor_id``
into the prompt but never reaches into the board layer.

The agent NEVER hangs and NEVER raises to its caller: on recursion-cap hit or
timeout it makes one forced tools-disabled final-answer call and parses that;
only if the forced call also fails (or any other exception occurs) does it
return a deterministic bounded fallback ``InterpretResult`` (verdict
``"invalid"``), mirroring the old failure-to-CustomNoteOp behavior.
"""

from __future__ import annotations

import json
import logging
import os
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FuturesTimeoutError
from typing import Any

from langchain.agents import create_agent
from langchain_core.messages import HumanMessage, SystemMessage
from langgraph.errors import GraphRecursionError

from agent.contract import InterpretResult
from agent.llm import get_chat_model
from agent.persona import build_system_prompt
from config import get_settings
from models.effects import CustomNoteOp, EffectProgram, RegisterHookOp, SnippetStep

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Hard caps — module-level so they are trivially configurable / overridable.
# ---------------------------------------------------------------------------
# Maximum number of reasoning/tool-call steps before we bail out. The compiled
# agent's recursion_limit counts super-steps; a value of 24 comfortably allows
# a handful of tool round-trips (each tool call is ~2 steps: model -> tool ->
# model) while still being low enough that hitting it forces a final answer
# rather than letting the agent burn an unbounded number of tool calls.
MAX_TOOL_CALLS: int = 24

# Wall-clock ceiling for a single interpretation, in seconds. Guards against a
# tool or model call that hangs on the network even when the step count is low.
AGENT_TIMEOUT_SECONDS: float = 600.0

# Extra wall-clock budget for the forced tools-disabled final-answer call made
# when the step cap or the timeout above is hit. Kept small and separate so a
# hung model can't turn a give-up into a second, equally long hang.
FORCED_FINAL_CALL_TIMEOUT_SECONDS: float = 30.0

FORCED_FINAL_INSTRUCTION = (
    "Budget exhausted — output your final JSON interpretation NOW using what you "
    "already know. Do not call any tools. Respond with ONLY the JSON object "
    "matching the contract."
)

REPAIR_INSTRUCTION = (
    "Your proposed effect failed sandbox validation or dry-run. Return one corrected final JSON object now. "
    "Do not call tools and do not explain the correction outside the JSON."
)

# Substrings a provider uses when it rejects image/vision input. Matched
# case-insensitively against the exception's string. Kept deliberately narrow:
# only these trigger the text-only retry, so an unrelated failure (network,
# tool, parse) is handled by the outer fallback instead of paying to re-run the
# whole agent — which would double the room-wide play freeze (bead phy.14).
_IMAGE_REJECTION_SIGNALS = (
    "image",
    "vision",
    "multimodal",
    "image_url",
)


def _is_image_rejection(exc: BaseException) -> bool:
    """Whether ``exc`` looks like the model rejecting the attached image input.

    Providers signal this inconsistently (no dedicated exception type), so we
    sniff the message. False on anything unrecognized so we never strip art for
    an error that has nothing to do with the image."""
    text = str(exc).lower()
    return any(signal in text for signal in _IMAGE_REJECTION_SIGNALS)


def _configure_langsmith() -> None:
    """Enable LangSmith tracing env vars IFF Settings.langsmith_tracing is True.

    Off by default and never requires network: when tracing is disabled we
    explicitly clear the env flag so a stray ``LANGCHAIN_TRACING_V2=true`` in the
    ambient environment cannot silently turn tracing on for our agent.
    """
    settings = get_settings()
    if not settings.langsmith_tracing:
        os.environ["LANGCHAIN_TRACING_V2"] = "false"
        os.environ["LANGSMITH_TRACING"] = "false"
        return
    os.environ["LANGCHAIN_TRACING_V2"] = "true"
    os.environ["LANGSMITH_TRACING"] = "true"
    if settings.langsmith_api_key:
        os.environ["LANGSMITH_API_KEY"] = settings.langsmith_api_key
    os.environ["LANGSMITH_PROJECT"] = settings.langsmith_project
    os.environ["LANGSMITH_ENDPOINT"] = settings.langsmith_endpoint


def build_agent(tools: list[Any] | None = None, model: Any | None = None, *, system_prompt: str | None = None):
    """Construct the single tool-calling agent.

    Args:
        tools: Tools to bind. Defaults to an EMPTY list (skeleton). Adding the real
            tools later is just ``build_agent(tools=[tool_a, tool_b, ...])``.
        model: A chat model to use. Defaults to :func:`agent.llm.get_chat_model`
            (the real provider-aware model). Tests inject a fake model here.
        system_prompt: Optional override for the system prompt. When None the caller
            (:func:`run_agent`) supplies a per-card prompt at invoke time; here we
            pass a minimal placeholder so ``build_agent`` is usable standalone.

    Returns:
        A compiled LangChain agent (a Pregel graph) with ``.invoke``.
    """
    tools = list(tools) if tools else []
    chat_model = model if model is not None else get_chat_model()
    return create_agent(
        model=chat_model,
        tools=tools,
        system_prompt=system_prompt or "You are the Game Master. Interpret the played card.",
    )


def _initial_user_content(title: str, card_art: str | None) -> str | list[dict[str, Any]]:
    """Content for the opening human message.

    Plain text when there is no art; otherwise a text block plus an
    ``image_url`` block carrying the card's PNG data-URL (the OpenAI
    chat-completions multimodal format, which ChatOpenAI passes through), so a
    vision-capable model can read the drawing.
    """
    text = f"Interpret the card titled {title!r} and produce the JSON result."
    if card_art is None:
        return text
    return [
        {"type": "text", "text": text},
        {"type": "image_url", "image_url": {"url": card_art}},
    ]


def _fallback_result(comment: str, note: str | None = None, persona_action: str = "do_nothing") -> InterpretResult:
    """Build the deterministic bounded fallback returned on cap/timeout/error.

    Mirrors the legacy failure behavior: verdict ``"invalid"`` with either a single
    :class:`~models.effects.CustomNoteOp` (so the play still logs something) or no
    program at all.
    """
    program = None
    if note is not None:
        program = EffectProgram(ops=[CustomNoteOp(note=note)])
    return InterpretResult(
        program=program,
        snippet=None,
        verdict="invalid",
        comment=comment,
        persona_action=persona_action,  # type: ignore[arg-type]
        agent_error=True,
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


def _extract_json_object(text: str) -> Any:
    """Parse the first contract-shaped JSON object in ``text``, tolerating prose and fences.

    Models sometimes wrap the contract JSON in commentary or a ```json fence;
    scanning forward with ``raw_decode`` recovers the object wherever it starts
    and ignores anything after it. Embedded candidates must carry the contract's
    ``verdict`` key so an inner op object (e.g. ``{\"op\": \"add_points\", ...}``)
    can't masquerade as a result. Raises ``json.JSONDecodeError`` when no
    suitable object exists.
    """
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        decoder = json.JSONDecoder()
        idx = text.find("{")
        while idx != -1:
            try:
                payload, _ = decoder.raw_decode(text, idx)
            except json.JSONDecodeError:
                pass
            else:
                if isinstance(payload, dict) and "verdict" in payload:
                    return payload
            idx = text.find("{", idx + 1)
        raise


def _parse_result(result: dict[str, Any]) -> InterpretResult:
    """Parse the agent's output into an InterpretResult.

    Preference order:
      1. A ``structured_response`` (when a future version wires response_format).
      2. The last AIMessage parsed as a JSON object matching the contract.

    On any parse failure this returns a bounded fallback rather than raising, so
    the caller always gets a well-formed InterpretResult.
    """
    structured = result.get("structured_response")
    if isinstance(structured, InterpretResult):
        return structured
    if isinstance(structured, dict):
        try:
            return InterpretResult.model_validate(structured)
        except Exception:  # noqa: BLE001 — malformed structured output degrades gracefully
            logger.warning("agent structured_response failed validation; falling back")

    text = _extract_final_text(result).strip()
    if text:
        try:
            payload = _extract_json_object(text)
        except json.JSONDecodeError:
            logger.warning("agent final message was not valid JSON; using it as a comment")
            return InterpretResult(verdict="invalid", comment=text[:280], persona_action="do_nothing", agent_error=True)
        try:
            return InterpretResult.model_validate(payload)
        except Exception:  # noqa: BLE001 — schema mismatch degrades gracefully
            logger.warning("agent final message JSON did not match InterpretResult")

    return _fallback_result(
        comment="I stared at that card and it stared back. Nothing happens.",
        note="Uninterpretable card: no effect applied.",
    )


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


def _build_forced_messages(
    system_prompt: str,
    title: str,
    progress: list[dict[str, Any]],
) -> list[Any]:
    """Assemble the message list for the tools-disabled forced-final call.

    Prefers the conversation accumulated while streaming the graph (captured in
    ``progress``, see :func:`run_agent`) so the forced call can use partial tool
    results. Degrades to a fresh system+user prompt (the bare-chat-model v1
    path) when nothing was captured.
    """
    if progress:
        messages = _sanitize_forced_messages(list(progress[-1].get("messages") or []))
        if messages:
            # create_agent injects the system prompt at model-call time; it is
            # never written into state["messages"], so it must be re-added here
            # or the forced call loses the persona and the InterpretResult contract.
            if not any(getattr(m, "type", None) == "system" for m in messages):
                messages = [SystemMessage(content=system_prompt), *messages]
            return [*messages, HumanMessage(content=FORCED_FINAL_INSTRUCTION)]

    return [
        SystemMessage(content=system_prompt),
        HumanMessage(content=f"Interpret the card titled {title!r} and produce the JSON result."),
        HumanMessage(content=FORCED_FINAL_INSTRUCTION),
    ]


def _forced_final_result(
    chat_model: Any,
    system_prompt: str,
    title: str,
    progress: list[dict[str, Any]],
    timeout: float,
) -> InterpretResult | None:
    """Make ONE tools-disabled LLM call to force a final answer out of a budget-exhausted agent.

    Returns None (never raises) when the forced call itself times out or errors,
    so the caller can degrade to the deterministic bounded fallback.
    """
    messages = _build_forced_messages(system_prompt, title, progress)

    # No context manager: __exit__ would call shutdown(wait=True) and block on
    # the still-running invoke, defeating the timeout below. shutdown(wait=False)
    # lets us return immediately and abandon the hung thread.
    pool = ThreadPoolExecutor(max_workers=1)
    try:
        future = pool.submit(chat_model.invoke, messages)
        try:
            response = future.result(timeout=timeout)
        except FuturesTimeoutError:
            logger.warning("forced final-answer call timed out after %.1fs", timeout)
            future.cancel()
            return None
        except Exception:  # noqa: BLE001 — forced call is best-effort
            logger.exception("forced final-answer call failed")
            return None
    finally:
        pool.shutdown(wait=False, cancel_futures=True)

    return _parse_result({"messages": [response]})


def _effect_validation_error(
    result: InterpretResult,
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
    result: InterpretResult,
    error: str,
    timeout: float,
) -> InterpretResult | None:
    messages = [
        SystemMessage(content=system_prompt),
        HumanMessage(
            content=(
                f"Card: {title}\nInvalid result: {result.model_dump_json()}\n"
                f"Validation error: {error}\n{REPAIR_INSTRUCTION}"
            )
        ),
    ]
    pool = ThreadPoolExecutor(max_workers=1)
    try:
        future = pool.submit(chat_model.invoke, messages)
        try:
            response = future.result(timeout=timeout)
        except FuturesTimeoutError:
            future.cancel()
            return None
        except Exception:  # noqa: BLE001
            logger.exception("effect repair call failed")
            return None
    finally:
        pool.shutdown(wait=False, cancel_futures=True)
    return _parse_result({"messages": [response]})


def _validate_or_repair_effect(
    result: InterpretResult,
    chat_model: Any,
    system_prompt: str,
    title: str,
    state: Any | None,
    actor_id: str | None,
    card_id: str | None,
    timeout: float,
) -> InterpretResult:
    error = _effect_validation_error(result, state, actor_id, card_id)
    if error is None:
        return result
    logger.warning("agent effect failed validation: %s", error)
    repaired = _repair_effect(chat_model, system_prompt, title, result, error, timeout)
    if repaired is not None and _effect_validation_error(repaired, state, actor_id, card_id) is None:
        return repaired
    return result.model_copy(update={"plan": None, "program": None, "snippet": None, "verdict": "invalid"})


def _assemble_tools(
    state: Any | None,
    actor_id: str | None,
    creator_id: str | None,
    extra: list[Any] | None,
    card_id: str | None = None,
    *,
    allow_persistent_tools: bool = True,
) -> list[Any]:
    """Build the final bound tool list for one interpretation.

    Order: the context-free defaults (:func:`agent.tools.get_default_tools`, which
    now includes ``read_engine_methods``) + the context-DEPENDENT
    ``read_game_state`` tool (only when ``state`` is provided, closed over the
    snapshot/actor/creator) + any explicitly-passed ``extra`` tools (an
    override/extra hook used by tests).

    Every stage is guarded so tool-building can NEVER break agent construction: a
    failing stage degrades to a smaller toolbox, mirroring get_default_tools'
    per-tool degradation.
    """
    tools: list[Any] = []

    try:
        from agent.tools import get_default_tools

        tools.extend(get_default_tools(allow_persistent_tools=allow_persistent_tools))
    except Exception:  # noqa: BLE001 — a broken default toolbox must not break the agent
        logger.warning("default tools unavailable; continuing with a reduced toolbox")

    if state is not None:
        try:
            from agent.tools.read_game_state import make_read_game_state_tool

            tools.append(make_read_game_state_tool(state, actor_id, creator_id, card_id))
        except Exception:  # noqa: BLE001 — the state tool is best-effort
            logger.warning("read_game_state tool unavailable; skipping")

        try:
            from agent.tools.read_game_history import make_read_game_history_tool

            tools.append(make_read_game_history_tool(state))
        except Exception:  # noqa: BLE001
            logger.warning("read_game_history tool unavailable; skipping")

        try:
            from agent.tools.dry_run_effect import make_dry_run_effect_tool

            tools.append(make_dry_run_effect_tool(state, actor_id, card_id))
        except Exception:  # noqa: BLE001
            logger.warning("dry_run_effect tool unavailable; skipping")

    if extra:
        tools.extend(extra)

    return tools


def run_agent(
    title: str,
    description: str,
    state: Any | None = None,
    actor_id: str | None = None,
    *,
    creator_id: str | None = None,
    card_id: str | None = None,
    card_art: str | None = None,
    tools: list[Any] | None = None,
    model: Any | None = None,
    timeout: float | None = None,
    max_tool_calls: int | None = None,
    forced_call_timeout: float | None = None,
    allow_persistent_tools: bool = True,
    config: dict[str, Any] | None = None,
) -> InterpretResult:
    """Interpret one card into an :class:`InterpretResult`. Never hangs, never raises.

    Args:
        title, description: The played card's text.
        state: Live game state (GameState or dict snapshot) threaded into the prompt.
            Never mutated; never sourced from the board layer here.
        actor_id: The id of the player who played the card.
        creator_id: The card's author id (drives do_nothing vs punish_author).
        card_id: The played card id, used to model its removal during effect dry-runs.
        card_art: The card's hand-drawn PNG data-URL (Room.card_art), passed as a
            side-channel — art never rides GameState. Attached to the model input
            as an image block ONLY when ``Settings.vision_enabled`` is on; ignored
            otherwise, so the default behavior is unchanged. If the configured
            model rejects image input (detected via :func:`_is_image_rejection`),
            the interpretation retries once text-only; unrelated failures skip
            the retry and degrade through the bounded fallback so they don't
            double the room-wide play freeze.
        tools: Tools to bind (default empty — real tools arrive in C2-C9).
        model: Chat model override for tests; defaults to the real provider model.
        timeout: Wall-clock ceiling in seconds (default :data:`AGENT_TIMEOUT_SECONDS`).
        max_tool_calls: Recursion/step cap (default :data:`MAX_TOOL_CALLS`).
        forced_call_timeout: Wall-clock ceiling for the forced tools-disabled
            final-answer call made on recursion-cap/timeout (default
            :data:`FORCED_FINAL_CALL_TIMEOUT_SECONDS`).
        allow_persistent_tools: Whether tools that write decision memory or
            capability telemetry may be bound. Preview callers disable them.
        config: Extra keys merged into the LangGraph run config passed to
            ``agent.stream`` (e.g. ``{"callbacks": [...]}`` for eval usage
            instrumentation). ``recursion_limit`` is always set from
            ``max_tool_calls`` and cannot be overridden here. Default None keeps
            production behavior byte-identical.

    Returns:
        A well-formed InterpretResult. On recursion cap or timeout, one forced
        tools-disabled final-answer call is made and its parsed result returned;
        only if that forced call also fails (or model-construction / any other
        exception occurs) is a deterministic bounded fallback with
        ``verdict="invalid"`` returned.
    """
    timeout = AGENT_TIMEOUT_SECONDS if timeout is None else timeout
    recursion_limit = MAX_TOOL_CALLS if max_tool_calls is None else max_tool_calls
    forced_call_timeout = FORCED_FINAL_CALL_TIMEOUT_SECONDS if forced_call_timeout is None else forced_call_timeout

    _configure_langsmith()

    if card_art is not None and not get_settings().vision_enabled:
        card_art = None

    system_prompt = build_system_prompt(
        title=title,
        description=description,
        state=state,
        actor_id=actor_id,
        creator_id=creator_id,
        has_art=card_art is not None,
    )

    bound_tools = _assemble_tools(
        state,
        actor_id,
        creator_id,
        tools,
        card_id,
        allow_persistent_tools=allow_persistent_tools,
    )

    try:
        chat_model = model if model is not None else get_chat_model()
        agent = build_agent(tools=bound_tools, model=chat_model, system_prompt=system_prompt)
    except Exception:  # noqa: BLE001 — model/agent construction must never escape
        logger.exception("agent construction failed; returning bounded fallback")
        return _fallback_result(
            comment="My brain isn't booting today. Consider yourself lucky.",
            note="Agent unavailable: no effect applied.",
        )

    inputs = {"messages": [("user", _initial_user_content(title, card_art))]}
    # recursion_limit is authoritative and set last so a passed-in config can add
    # callbacks/tags/metadata but never weaken the step cap.
    config = {**(config or {}), "recursion_limit": recursion_limit}

    # progress accumulates every intermediate graph state so that, if the graph
    # is interrupted by the recursion cap or the timeout below, we still have the
    # conversation-so-far to hand to the forced final-answer call.
    progress: list[dict[str, Any]] = []

    def _stream_agent() -> dict[str, Any] | None:
        for chunk in agent.stream(inputs, config, stream_mode="values"):
            progress.append(chunk)
        return progress[-1] if progress else None

    # Run the (synchronous) streaming loop on a worker thread so we can enforce a
    # hard wall-clock timeout even if the underlying call blocks on the network.
    # No context manager: __exit__ would call shutdown(wait=True) and block on
    # the still-running stream, defeating the timeout below. shutdown(wait=False)
    # lets us return immediately and abandon the hung thread.
    pool = ThreadPoolExecutor(max_workers=1)
    try:
        future = pool.submit(_stream_agent)
        try:
            result = future.result(timeout=timeout)
        except FuturesTimeoutError:
            logger.warning("agent timed out after %.1fs; forcing a final answer", timeout)
            future.cancel()
            forced = _forced_final_result(chat_model, system_prompt, title, progress, forced_call_timeout)
            if forced is not None:
                return _validate_or_repair_effect(
                    forced,
                    chat_model,
                    system_prompt,
                    title,
                    state,
                    actor_id,
                    card_id,
                    forced_call_timeout,
                )
            return _fallback_result(
                comment="Figuring out your card took so long I lost interest. Nothing happens.",
                note="Interpretation timed out: no effect applied.",
            )
        except GraphRecursionError:
            logger.warning("agent hit recursion cap (%d); forcing a final answer", recursion_limit)
            forced = _forced_final_result(chat_model, system_prompt, title, progress, forced_call_timeout)
            if forced is not None:
                return _validate_or_repair_effect(
                    forced,
                    chat_model,
                    system_prompt,
                    title,
                    state,
                    actor_id,
                    card_id,
                    forced_call_timeout,
                )
            return _fallback_result(
                comment="I went in circles trying to make sense of that. I give up.",
                note="Interpretation exceeded step budget: no effect applied.",
            )
        except Exception as exc:  # noqa: BLE001 — any agent-internal error degrades gracefully
            if card_art is not None and _is_image_rejection(exc):
                # The model rejected the attached image; a drawing must never
                # fail the play, so re-run once text-only. Only image-rejection
                # errors get this retry — anything else falls through to the
                # bounded fallback rather than doubling the play-freeze duration.
                logger.warning("agent invoke rejected card art; retrying text-only", exc_info=True)
                return run_agent(
                    title,
                    description,
                    state,
                    actor_id,
                    creator_id=creator_id,
                    card_id=card_id,
                    tools=tools,
                    model=model,
                    timeout=timeout,
                    max_tool_calls=max_tool_calls,
                    forced_call_timeout=forced_call_timeout,
                    allow_persistent_tools=allow_persistent_tools,
                    config=config,
                )
            logger.exception("agent invoke failed; returning bounded fallback")
            return _fallback_result(
                comment="Something broke, and I'm choosing to blame that card.",
                note="Interpretation error: no effect applied.",
            )
    finally:
        pool.shutdown(wait=False, cancel_futures=True)

    return _validate_or_repair_effect(
        _parse_result(result),
        chat_model,
        system_prompt,
        title,
        state,
        actor_id,
        card_id,
        forced_call_timeout,
    )
