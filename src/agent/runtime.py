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

The stream-under-timeout / forced-final-call machinery lives in
:mod:`agent.stage_runner` (:func:`run_agent` runs through :func:`run_stage`);
this module keeps the InterpretResult-specific parsing and fallbacks.
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any

from langchain.agents import create_agent

from agent import stage_runner
from agent.contract import InterpretResult
from agent.llm import get_chat_model
from agent.persona import build_system_prompt
from agent.stage_runner import (  # noqa: F401 — re-exported for backward compatibility
    FORCED_FINAL_INSTRUCTION,
    REPAIR_INSTRUCTION,
)
from agent.stage_runner import _extract_final_text, run_stage
from config import get_settings
from engine.history import fallback_counts
from models.effects import CustomNoteOp, EffectProgram

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Hard caps — module-level so they are trivially configurable / overridable.
# ---------------------------------------------------------------------------
# Maximum number of reasoning/tool-call steps before we bail out. The compiled
# agent's recursion_limit counts super-steps; a value of 24 comfortably allows
# a handful of tool round-trips (each tool call is ~2 steps: model -> tool ->
# model) while still being low enough that hitting it forces a final answer
# rather than letting the agent burn an unbounded number of tool calls.
MAX_TOOL_CALLS: int = 12

# Wall-clock ceiling for a single interpretation, in seconds. Guards against a
# tool or model call that hangs on the network even when the step count is low.
AGENT_TIMEOUT_SECONDS: float = 600.0

# Extra wall-clock budget for the forced tools-disabled final-answer call made
# when the step cap or the timeout above is hit. Kept small and separate so a
# hung model can't turn a give-up into a second, equally long hang.
FORCED_FINAL_CALL_TIMEOUT_SECONDS: float = 30.0

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


_CONTRACT_KEYS = ("verdict", "plan", "resolution_plan", "program", "snippet")


def _is_contract_shaped(payload: Any) -> bool:
    """True for a result object, False for an inner op/step dict.

    A result carries at least one contract field (or a top-level ``steps`` list,
    i.e. a bare plan). An op (``{"op": ...}``) or step (``{"kind": "ops", ...}``)
    dict has none of these, so it can't masquerade as a result.
    """
    if not isinstance(payload, dict):
        return False
    if any(key in payload for key in _CONTRACT_KEYS):
        return True
    return isinstance(payload.get("steps"), list)


def _normalise_contract_payload(payload: Any) -> Any:
    """Coerce a loosely-shaped result dict toward the ``InterpretResult`` schema.

    Accepts a plan emitted under the scorer-facing ``resolution_plan`` key or as
    a bare plan object, and treats a payload that carries an effect but no
    explicit verdict as a success (rather than the ``invalid`` field default).
    """
    if not isinstance(payload, dict):
        return payload
    payload = dict(payload)
    if "plan" not in payload:
        if isinstance(payload.get("resolution_plan"), dict):
            payload["plan"] = payload.pop("resolution_plan")
        elif isinstance(payload.get("steps"), list):
            payload["plan"] = {"steps": payload.pop("steps")}
    if "verdict" not in payload and any(payload.get(key) for key in ("plan", "program", "snippet")):
        payload["verdict"] = "ok"
    return payload


def _extract_json_object(text: str) -> Any:
    """Parse the first contract-shaped JSON object in ``text``, tolerating prose and fences.

    See :func:`agent.stage_runner._extract_json_object`; candidates here must be
    contract-shaped (:func:`_is_contract_shaped`) so an inner op object can't
    masquerade as a result. Raises ``json.JSONDecodeError`` when no suitable
    object exists.
    """
    return stage_runner._extract_json_object(text, _is_contract_shaped)


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
            return InterpretResult.model_validate(_normalise_contract_payload(payload))
        except Exception:  # noqa: BLE001 — schema mismatch degrades gracefully
            logger.warning("agent final message JSON did not match InterpretResult")

    return _fallback_result(
        comment="A card so mysterious even I blinked. Nothing happens.",
        note="Uninterpretable card: no effect applied.",
    )


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
    return stage_runner._forced_final_result(
        chat_model, system_prompt, _initial_user_content(title, None), progress, timeout, _parse_result
    )


_effect_validation_error = stage_runner._effect_validation_error


def _repair_effect(
    chat_model: Any,
    system_prompt: str,
    title: str,
    result: InterpretResult,
    error: str,
    timeout: float,
) -> InterpretResult | None:
    return stage_runner._repair_effect(chat_model, system_prompt, title, result, error, timeout, _parse_result)


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
    return stage_runner._validate_or_repair_effect(
        result, chat_model, system_prompt, title, state, actor_id, card_id, timeout, _parse_result
    )


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

    Dispatch: when ``Settings.interpret_pipeline_enabled`` is True this delegates
    to :func:`agent.pipeline.run_pipeline` (the three-stage intent -> planner ->
    coder pipeline), forwarding every argument unchanged; otherwise it runs the
    legacy single tool-calling agent (:func:`_run_single_agent`). Callers that
    must pin one path regardless of the flag call ``run_pipeline`` /
    ``_run_single_agent`` directly (see ``evals.runner``).

    Args:
        title, description: The played card's text.
        state: Live game state (GameState or dict snapshot) threaded into the prompt.
            Never mutated; never sourced from the board layer here.
        actor_id: The id of the player who played the card.
        creator_id: The card's author id (decides who receives the consolation boon
            and whether the rare abusive-card punish_author branch applies).
        card_id: The played card id, used to model its removal during effect dry-runs.
        card_art: The card's hand-drawn PNG data-URL (Room.card_art), passed as a
            side-channel — art never rides GameState. Attached to the model input
            as an image block ONLY when ``Settings.vision_enabled`` is on; ignored
            otherwise, so the default behavior is unchanged. If the configured
            model rejects image input (detected via :func:`_is_image_rejection`),
            the interpretation retries once text-only; unrelated failures skip
            the retry and degrade through the bounded fallback so they don't
            double the room-wide play freeze.
        tools: Explicit tool list to bind AS-IS, replacing the assembled
            production toolbox (callers like the eval runner pre-filter it; an
            empty list binds no tools). None (the default) assembles the
            production toolbox via :func:`_assemble_tools`.
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
    if get_settings().interpret_pipeline_enabled:
        # Lazy import: pipeline imports runtime helpers, so importing it at
        # module top would create an import cycle.
        from agent import pipeline

        return pipeline.run_pipeline(
            title,
            description,
            state,
            actor_id,
            creator_id=creator_id,
            card_id=card_id,
            card_art=card_art,
            tools=tools,
            model=model,
            timeout=timeout,
            max_tool_calls=max_tool_calls,
            forced_call_timeout=forced_call_timeout,
            allow_persistent_tools=allow_persistent_tools,
            config=config,
        )
    return _run_single_agent(
        title,
        description,
        state,
        actor_id,
        creator_id=creator_id,
        card_id=card_id,
        card_art=card_art,
        tools=tools,
        model=model,
        timeout=timeout,
        max_tool_calls=max_tool_calls,
        forced_call_timeout=forced_call_timeout,
        allow_persistent_tools=allow_persistent_tools,
        config=config,
    )


def _run_single_agent(
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
    """The legacy single tool-calling agent — :func:`run_agent`'s flag-off body.

    Same parameters and guarantees as :func:`run_agent` (see its docstring);
    this path never consults ``interpret_pipeline_enabled``.
    """
    timeout = AGENT_TIMEOUT_SECONDS if timeout is None else timeout
    recursion_limit = MAX_TOOL_CALLS if max_tool_calls is None else max_tool_calls
    forced_call_timeout = FORCED_FINAL_CALL_TIMEOUT_SECONDS if forced_call_timeout is None else forced_call_timeout

    _configure_langsmith()

    settings = get_settings()
    if card_art is not None and not settings.vision_enabled:
        card_art = None

    author_fallbacks = 0
    if state is not None and creator_id:
        author_fallbacks = fallback_counts(state).get(creator_id, 0)
    threshold = settings.struggling_author_threshold
    struggling_author = bool(threshold) and author_fallbacks >= threshold

    system_prompt = build_system_prompt(
        title=title,
        description=description,
        state=state,
        actor_id=actor_id,
        creator_id=creator_id,
        has_art=card_art is not None,
        struggling_author=struggling_author,
        author_fallbacks=author_fallbacks,
    )

    # An explicit tool list is authoritative — the caller already decided the
    # toolbox (e.g. the eval runner's enabled_tools filter). Only assemble the
    # production toolbox when no list was passed.
    if tools is not None:
        bound_tools = list(tools)
    else:
        bound_tools = _assemble_tools(
            state,
            actor_id,
            creator_id,
            None,
            card_id,
            allow_persistent_tools=allow_persistent_tools,
        )

    try:
        chat_model = model if model is not None else get_chat_model()
        agent = build_agent(tools=bound_tools, model=chat_model, system_prompt=system_prompt)
    except Exception:  # noqa: BLE001 — model/agent construction must never escape
        logger.exception("agent construction failed; returning bounded fallback")
        return _fallback_result(
            comment="My brain isn't booting today. That one's on me, not your card.",
            note="Agent unavailable: no effect applied.",
        )

    def _parse(result: dict[str, Any]) -> InterpretResult:
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

    def _on_failure(kind: str, exc: BaseException | None) -> InterpretResult:
        if kind == "timeout":
            return _fallback_result(
                comment="Your card sent me on a journey I wasn't prepared for. Nothing happens.",
                note="Interpretation timed out: no effect applied.",
            )
        if kind == "recursion":
            return _fallback_result(
                comment="I went in circles trying to honor that card. It defeated me fairly.",
                note="Interpretation exceeded step budget: no effect applied.",
            )
        if card_art is not None and exc is not None and _is_image_rejection(exc):
            # The model rejected the attached image; a drawing must never
            # fail the play, so re-run once text-only. Only image-rejection
            # errors get this retry — anything else falls through to the
            # bounded fallback rather than doubling the play-freeze duration.
            logger.warning("agent invoke rejected card art; retrying text-only", exc_info=True)
            return _run_single_agent(
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
            comment="Something broke on my end. Your card is legally innocent.",
            note="Interpretation error: no effect applied.",
        )

    result = run_stage(
        system_prompt,
        _initial_user_content(title, card_art),
        bound_tools,
        chat_model,
        InterpretResult,
        timeout=timeout,
        max_steps=recursion_limit,
        forced_call_timeout=forced_call_timeout,
        config=config,
        agent=agent,
        parse=_parse,
        on_failure=_on_failure,
    )
    if result is None:
        # Unreachable with the hooks above (they always return an InterpretResult);
        # kept as a hard floor on the never-returns-None contract.
        return _fallback_result(
            comment="Something broke on my end. Your card is legally innocent.",
            note="Interpretation error: no effect applied.",
        )
    return result
