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

The agent NEVER hangs and NEVER raises to its caller: on recursion-cap hit,
timeout, or any exception it returns a deterministic bounded fallback
``InterpretResult`` (verdict ``"invalid"``), mirroring the old
failure-to-CustomNoteOp behavior.
"""

from __future__ import annotations

import json
import logging
import os
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FuturesTimeoutError
from typing import Any

from langchain.agents import create_agent
from langgraph.errors import GraphRecursionError

from agent.contract import InterpretResult
from agent.llm import get_chat_model
from agent.persona import build_system_prompt
from config import get_settings
from models.effects import CustomNoteOp, EffectProgram

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Hard caps — module-level so they are trivially configurable / overridable.
# ---------------------------------------------------------------------------
# Maximum number of reasoning/tool-call steps before we bail out. The compiled
# agent's recursion_limit counts super-steps; a value of 8 comfortably allows a
# few tool round-trips (each tool call is ~2 steps: model -> tool -> model).
MAX_TOOL_CALLS: int = 8

# Wall-clock ceiling for a single interpretation, in seconds. Guards against a
# tool or model call that hangs on the network even when the step count is low.
AGENT_TIMEOUT_SECONDS: float = 20.0


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
        # Tolerate a ```json fence around the object.
        if text.startswith("```"):
            text = text.strip("`")
            text = text[text.find("{") :] if "{" in text else text
        try:
            payload = json.loads(text)
            return InterpretResult.model_validate(payload)
        except json.JSONDecodeError:
            logger.warning("agent final message was not valid JSON; using it as a comment")
            return InterpretResult(verdict="invalid", comment=text[:280], persona_action="do_nothing")
        except Exception:  # noqa: BLE001 — schema mismatch degrades gracefully
            logger.warning("agent final message JSON did not match InterpretResult")

    return _fallback_result(
        comment="I stared at that card and it stared back. Nothing happens.",
        note="Uninterpretable card: no effect applied.",
    )


def _assemble_tools(
    state: Any | None,
    actor_id: str | None,
    creator_id: str | None,
    extra: list[Any] | None,
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

        tools.extend(get_default_tools())
    except Exception:  # noqa: BLE001 — a broken default toolbox must not break the agent
        logger.warning("default tools unavailable; continuing with a reduced toolbox")

    if state is not None:
        try:
            from agent.tools.read_game_state import make_read_game_state_tool

            tools.append(make_read_game_state_tool(state, actor_id, creator_id))
        except Exception:  # noqa: BLE001 — the state tool is best-effort
            logger.warning("read_game_state tool unavailable; skipping")

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
    tools: list[Any] | None = None,
    model: Any | None = None,
    timeout: float | None = None,
    max_tool_calls: int | None = None,
) -> InterpretResult:
    """Interpret one card into an :class:`InterpretResult`. Never hangs, never raises.

    Args:
        title, description: The played card's text.
        state: Live game state (GameState or dict snapshot) threaded into the prompt.
            Never mutated; never sourced from the board layer here.
        actor_id: The id of the player who played the card.
        creator_id: The card's author id (drives do_nothing vs punish_author).
        tools: Tools to bind (default empty — real tools arrive in C2-C9).
        model: Chat model override for tests; defaults to the real provider model.
        timeout: Wall-clock ceiling in seconds (default :data:`AGENT_TIMEOUT_SECONDS`).
        max_tool_calls: Recursion/step cap (default :data:`MAX_TOOL_CALLS`).

    Returns:
        A well-formed InterpretResult. On recursion cap, timeout, model-construction
        failure, or any other exception, a deterministic bounded fallback with
        ``verdict="invalid"`` is returned.
    """
    timeout = AGENT_TIMEOUT_SECONDS if timeout is None else timeout
    recursion_limit = MAX_TOOL_CALLS if max_tool_calls is None else max_tool_calls

    _configure_langsmith()

    system_prompt = build_system_prompt(
        title=title,
        description=description,
        state=state,
        actor_id=actor_id,
        creator_id=creator_id,
    )

    bound_tools = _assemble_tools(state, actor_id, creator_id, tools)

    try:
        agent = build_agent(tools=bound_tools, model=model, system_prompt=system_prompt)
    except Exception:  # noqa: BLE001 — model/agent construction must never escape
        logger.exception("agent construction failed; returning bounded fallback")
        return _fallback_result(
            comment="My brain isn't booting today. Consider yourself lucky.",
            note="Agent unavailable: no effect applied.",
        )

    inputs = {"messages": [("user", f"Interpret the card titled {title!r} and produce the JSON result.")]}
    config = {"recursion_limit": recursion_limit}

    # Run the (synchronous) invoke on a worker thread so we can enforce a hard
    # wall-clock timeout even if the underlying call blocks on the network.
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(agent.invoke, inputs, config)
        try:
            result = future.result(timeout=timeout)
        except FuturesTimeoutError:
            logger.warning("agent timed out after %.1fs; returning bounded fallback", timeout)
            future.cancel()
            return _fallback_result(
                comment="You took so long I lost interest. Nothing happens.",
                note="Interpretation timed out: no effect applied.",
            )
        except GraphRecursionError:
            logger.warning("agent hit recursion cap (%d); returning bounded fallback", recursion_limit)
            return _fallback_result(
                comment="I went in circles trying to make sense of that. I give up.",
                note="Interpretation exceeded step budget: no effect applied.",
            )
        except Exception:  # noqa: BLE001 — any agent-internal error degrades gracefully
            logger.exception("agent invoke failed; returning bounded fallback")
            return _fallback_result(
                comment="Something broke, and I'm choosing to blame that card.",
                note="Interpretation error: no effect applied.",
            )

    return _parse_result(result)
