"""agent.pipeline — the three-agent LangGraph interpretation pipeline (bead 47b.5).

A ``langgraph.graph.StateGraph`` with nodes ``intent`` -> ``planner`` ->
``coder`` -> ``validate_repair`` -> ``finalize`` and conditional routing:

- an unusable intent (timeout/garbage) short-circuits to ``finalize`` with the
  bounded fallback;
- a truly undecipherable card the persona chose to ``do_nothing`` about skips
  mechanics entirely (empty program, intent's comment preserved);
- a ``punish_author`` intent is rewritten into a REAL point-docking effect and
  still flows through planner and coder;
- a failed planner degrades to a stub plan (the coder designs mechanics from
  the intent alone); an infeasible plan finalizes as ``invalid`` with a
  CustomNoteOp naming the intended effect so the triage/capability-wish flow
  stays fed.

Each node runs one bounded ReAct stage (:func:`agent.stage_runner.run_stage`)
under a per-stage budget clipped to the pipeline-wide deadline. The persona
lives ONLY in the intent stage: the final :class:`InterpretResult` combines the
coder's effect fields with the INTENT's ``comment`` and ``persona_action``.

:func:`run_pipeline` mirrors :func:`agent.runtime.run_agent`'s parameter
surface and its guarantees: it never hangs and never raises — on total failure
it returns a bounded fallback with ``agent_error=True``.

Layering: this module may import ``engine``, ``models``, ``config`` and
``logging_config`` but NEVER ``board``.
"""

from __future__ import annotations

import json
import logging
import operator
import time
from collections.abc import Callable
from typing import Annotated, Any, TypedDict

from langgraph.graph import END, START, StateGraph

from agent import runtime, stage_runner
from agent.contract import CardIntent, InterpretResult, MechanicsPlan
from agent.llm import get_chat_model
from agent.stage_prompts import build_coder_prompt, build_intent_prompt, build_planner_prompt
from agent.stage_runner import run_stage
from config import get_settings
from engine.history import fallback_counts
from models.effects import CustomNoteOp, EffectProgram

logger = logging.getLogger(__name__)

# Per-stage defaults. Timeouts sum to 540s (< runtime.AGENT_TIMEOUT_SECONDS) and
# step caps to 24; caller-passed timeout/max_tool_calls are split across the
# stages by these same ratios so eval sweeps over the knobs stay meaningful.
INTENT_TIMEOUT_SECONDS: float = 120.0
PLANNER_TIMEOUT_SECONDS: float = 180.0
CODER_TIMEOUT_SECONDS: float = 240.0
INTENT_MAX_STEPS: int = 6
PLANNER_MAX_STEPS: int = 8
CODER_MAX_STEPS: int = 10

# A trivial-complexity intent shrinks the downstream caps: one obvious effect
# needs neither eight planning steps nor ten coding ones. Caps are LangGraph
# super-steps (a tool round-trip costs ~2), so anything below 4 starves a stage
# of even one tool call — the first smoke A/B showed 2/4 forcing garbage
# final answers out of otherwise-correct stages.
TRIVIAL_PLANNER_MAX_STEPS: int = 4
TRIVIAL_CODER_MAX_STEPS: int = 6

_STAGE_TIMEOUTS: dict[str, float] = {
    "intent": INTENT_TIMEOUT_SECONDS,
    "planner": PLANNER_TIMEOUT_SECONDS,
    "coder": CODER_TIMEOUT_SECONDS,
}
_STAGE_STEPS: dict[str, int] = {
    "intent": INTENT_MAX_STEPS,
    "planner": PLANNER_MAX_STEPS,
    "coder": CODER_MAX_STEPS,
}
_TIMEOUT_TOTAL = sum(_STAGE_TIMEOUTS.values())
_STEPS_TOTAL = sum(_STAGE_STEPS.values())

# Which tools each stage may bind, by tool NAME. The intent stage resolves
# references and reads the board; the planner designs mechanics against the
# engine; the coder writes and dry-runs the effect. The persistent-memory pair
# is additionally gated by ``allow_persistent_tools``.
STAGE_TOOL_NAMES: dict[str, tuple[str, ...]] = {
    "intent": ("web_search", "mtg_lookup", "card_rag_hybrid", "game_rules", "read_game_state", "recall_decisions"),
    "planner": ("game_rules", "read_engine_methods", "read_game_state", "read_game_history", "card_rag_hybrid"),
    "coder": ("dry_run_effect", "read_engine_methods", "read_game_state", "remember_decision"),
}
_PERSISTENT_TOOL_NAMES = frozenset({"recall_decisions", "remember_decision"})

_STAGE_MODEL_SETTINGS: dict[str, str] = {
    "intent": "intent_agent_model",
    "planner": "planner_agent_model",
    "coder": "coder_agent_model",
}


class PipelineState(TypedDict, total=False):
    """The LangGraph state threaded through the interpretation pipeline.

    Inputs are written once by :func:`run_pipeline`; artifacts (``intent``,
    ``plan``, ``draft``, ``result``) are each written by exactly one node.
    ``stage_errors`` accumulates across nodes via the ``operator.add`` reducer.
    """

    title: str
    description: str
    card_art: str | None
    actor_id: str | None
    creator_id: str | None
    card_id: str | None
    game_state: Any
    allow_persistent_tools: bool
    model: Any
    config: dict[str, Any] | None
    tools: list[Any] | None
    struggling_author: bool
    author_fallbacks: int
    budgets: dict[str, Any]

    intent: CardIntent | None
    plan: MechanicsPlan | None
    draft: InterpretResult | None
    coder_prompt: str
    result: InterpretResult | None

    deadline: float
    stage_errors: Annotated[list[str], operator.add]


def _stage_budgets(timeout: float | None, max_tool_calls: int | None, forced_call_timeout: float) -> dict[str, Any]:
    """Split the caller's budget knobs across the three stages.

    A caller-passed ``timeout`` is divided by the 120/180/240 default ratio; a
    caller-passed ``max_tool_calls`` by the 6/8/10 split (each stage keeps at
    least one step). With both None the stage defaults apply unchanged.
    """
    time_scale = 1.0 if timeout is None else timeout / _TIMEOUT_TOTAL
    budgets: dict[str, Any] = {"forced_call_timeout": forced_call_timeout}
    for stage, stage_timeout in _STAGE_TIMEOUTS.items():
        steps = _STAGE_STEPS[stage]
        if max_tool_calls is not None:
            steps = max(1, round(max_tool_calls * steps / _STEPS_TOTAL))
        budgets[stage] = {"timeout": stage_timeout * time_scale, "max_steps": steps}
    return budgets


def _node_budget(state: PipelineState, stage: str) -> tuple[float, int]:
    """This node's wall-clock budget (clipped to the pipeline deadline) and step cap."""
    budget = (state.get("budgets") or {}).get(stage) or {}
    stage_timeout = float(budget.get("timeout", _STAGE_TIMEOUTS[stage]))
    max_steps = int(budget.get("max_steps", _STAGE_STEPS[stage]))
    deadline = state.get("deadline")
    if deadline is not None:
        stage_timeout = min(stage_timeout, deadline - time.monotonic())
    return stage_timeout, max_steps


def _forced_timeout(state: PipelineState) -> float:
    budgets = state.get("budgets") or {}
    return float(budgets.get("forced_call_timeout", runtime.FORCED_FINAL_CALL_TIMEOUT_SECONDS))


def _stage_model(state: PipelineState, stage: str) -> Any | None:
    """The chat model one stage runs on.

    A caller-passed ``model`` is authoritative for ALL stages (tests inject
    fakes that way). Otherwise a non-empty per-stage Settings override
    (``intent_agent_model`` / ``planner_agent_model`` / ``coder_agent_model``)
    builds that stage's model via :func:`agent.llm.get_chat_model`; blank
    returns None so :func:`run_stage` falls back to the shared default.
    """
    model = state.get("model")
    if model is not None:
        return model
    name = getattr(get_settings(), _STAGE_MODEL_SETTINGS[stage])
    return get_chat_model(model_name=name) if name else None


def _tool_builders(state: PipelineState) -> dict[str, Callable[[], Any]]:
    """Name -> zero-arg builder for every tool the pipeline knows how to bind.

    The context-free entries reuse the same factories ``agent.tools`` exposes;
    the context-bound entries close over the live game state exactly like
    :func:`agent.runtime._assemble_tools` and exist only when a state snapshot
    was provided.
    """
    from agent.tools.agent_memory import recall_decisions, remember_decision
    from agent.tools.card_rag_hybrid import get_card_rag_hybrid_tool
    from agent.tools.game_rules import get_game_rules_tool
    from agent.tools.mtg_lookup import get_mtg_lookup_tool
    from agent.tools.read_engine_methods import get_read_engine_methods_tool
    from agent.tools.web_search import get_web_search_tool

    builders: dict[str, Callable[[], Any]] = {
        "web_search": get_web_search_tool,
        "mtg_lookup": get_mtg_lookup_tool,
        "card_rag_hybrid": get_card_rag_hybrid_tool,
        "game_rules": get_game_rules_tool,
        "read_engine_methods": get_read_engine_methods_tool,
        "recall_decisions": lambda: recall_decisions,
        "remember_decision": lambda: remember_decision,
    }

    game_state = state.get("game_state")
    if game_state is not None:
        actor_id = state.get("actor_id")
        creator_id = state.get("creator_id")
        card_id = state.get("card_id")

        def _read_game_state():
            from agent.tools.read_game_state import make_read_game_state_tool

            return make_read_game_state_tool(game_state, actor_id, creator_id, card_id)

        def _read_game_history():
            from agent.tools.read_game_history import make_read_game_history_tool

            return make_read_game_history_tool(game_state)

        def _dry_run_effect():
            from agent.tools.dry_run_effect import make_dry_run_effect_tool

            return make_dry_run_effect_tool(game_state, actor_id, card_id)

        builders["read_game_state"] = _read_game_state
        builders["read_game_history"] = _read_game_history
        builders["dry_run_effect"] = _dry_run_effect

    return builders


def _stage_tools(stage: str, state: PipelineState) -> list[Any]:
    """The tool list for one stage. Never raises; failures shrink the toolbox.

    An explicit caller-passed ``tools`` list is authoritative and handed to ALL
    THREE stages unchanged: callers like the eval runner already decided the
    toolbox, and silently dropping entries by name-filtering would make sweeps
    lie about what was available.
    """
    explicit = state.get("tools")
    if explicit is not None:
        return list(explicit)

    try:
        builders = _tool_builders(state)
    except Exception:  # noqa: BLE001 — a broken toolbox must not break the stage
        logger.warning("%s stage toolbox unavailable; continuing without tools", stage)
        return []

    allow_persistent = state.get("allow_persistent_tools", False)
    tools: list[Any] = []
    for name in STAGE_TOOL_NAMES[stage]:
        if name in _PERSISTENT_TOOL_NAMES and not allow_persistent:
            continue
        builder = builders.get(name)
        if builder is None:
            continue
        try:
            tools.append(builder())
        except Exception:  # noqa: BLE001 — one failing tool degrades to a smaller toolbox
            logger.warning("tool %s unavailable for %s stage; skipping", name, stage)
    return tools


def _parse_coder(result: dict[str, Any]) -> InterpretResult | None:
    """Parse the coder's effect-only JSON into an InterpretResult, or None.

    Reuses the legacy runtime's contract-shaped extraction and normalisation
    (``resolution_plan``/bare-``steps`` recovery, implicit ``verdict: ok`` when
    an effect is present) but returns None instead of a canned fallback so the
    pipeline decides its own — comment-preserving — degradation.
    """
    structured = result.get("structured_response")
    if isinstance(structured, InterpretResult):
        return structured

    text = stage_runner._extract_final_text(result).strip()
    if not text:
        return None
    try:
        payload = runtime._extract_json_object(text)
    except json.JSONDecodeError:
        logger.warning("coder final message was not valid JSON")
        return None
    try:
        return InterpretResult.model_validate(runtime._normalise_contract_payload(payload))
    except Exception:  # noqa: BLE001 — schema mismatch degrades gracefully
        logger.warning("coder final message JSON did not match InterpretResult")
        return None


def _working_intent(state: PipelineState) -> CardIntent:
    """The intent downstream stages plan/code against.

    A ``punish_author`` intent (the rare abusive-card branch) is rewritten so
    the mechanics stages build a REAL point-docking effect against the card's
    author instead of implementing the abusive card itself. The original
    ``comment``/``persona_action`` survive the copy for ``finalize``.
    """
    intent = state["intent"]
    if intent is None or intent.persona_action != "punish_author":
        return intent
    creator_id = state.get("creator_id")
    author = f"the card's author (player id:{creator_id})" if creator_id else "the card's author"
    return intent.model_copy(
        update={
            "summary": f"Punish an abusive card: subtract points from {author}. "
            "The docking must be a real, applied effect — not a note.",
            "effects": [f"subtract points from {author}"],
            "targets": author,
            "persistence": "immediate",
            "ambiguity": "clear",
        }
    )


def _intent_node(state: PipelineState) -> dict[str, Any]:
    """Run the persona/intent stage; on image rejection retry once text-only."""
    title = state.get("title", "")
    tools = _stage_tools("intent", state)

    def _attempt(art: str | None) -> tuple[CardIntent | None, BaseException | None]:
        budget, max_steps = _node_budget(state, "intent")
        if budget <= 0:
            return None, None
        prompt = build_intent_prompt(
            title,
            state.get("description", ""),
            state=state.get("game_state"),
            actor_id=state.get("actor_id"),
            creator_id=state.get("creator_id"),
            has_art=art is not None,
            struggling_author=state.get("struggling_author", False),
            author_fallbacks=state.get("author_fallbacks", 0),
        )
        failure: list[BaseException | None] = [None]

        def _on_failure(kind: str, exc: BaseException | None) -> None:
            failure[0] = exc
            return None

        intent = run_stage(
            prompt,
            runtime._initial_user_content(title, art),
            tools,
            _stage_model(state, "intent"),
            CardIntent,
            timeout=budget,
            max_steps=max_steps,
            forced_call_timeout=_forced_timeout(state),
            config=state.get("config"),
            on_failure=_on_failure,
        )
        return intent, failure[0]

    card_art = state.get("card_art")
    intent, exc = _attempt(card_art)
    if intent is None and card_art is not None and exc is not None and runtime._is_image_rejection(exc):
        logger.warning("intent stage rejected card art; retrying text-only")
        intent, _ = _attempt(None)
    if intent is None:
        return {"stage_errors": ["intent: stage produced no CardIntent"]}
    return {"intent": intent}


def _route_after_intent(state: PipelineState) -> str:
    intent = state.get("intent")
    if intent is None:
        return "finalize"
    if intent.ambiguity == "undecipherable" and intent.persona_action == "do_nothing":
        return "finalize"
    return "planner"


def _planner_node(state: PipelineState) -> dict[str, Any]:
    """Run the mechanics-planning stage against the (possibly rewritten) intent."""
    budget, max_steps = _node_budget(state, "planner")
    if budget <= 0:
        return {"stage_errors": ["planner: pipeline deadline exhausted"]}
    intent = _working_intent(state)
    if intent.complexity == "trivial":
        max_steps = min(max_steps, TRIVIAL_PLANNER_MAX_STEPS)
    prompt = build_planner_prompt(
        intent,
        state=state.get("game_state"),
        actor_id=state.get("actor_id"),
        creator_id=state.get("creator_id"),
    )
    plan = run_stage(
        prompt,
        f"Design the mechanics plan for the card titled {state.get('title', '')!r} and produce the JSON result.",
        _stage_tools("planner", state),
        _stage_model(state, "planner"),
        MechanicsPlan,
        timeout=budget,
        max_steps=max_steps,
        forced_call_timeout=_forced_timeout(state),
        config=state.get("config"),
    )
    if plan is None:
        return {"stage_errors": ["planner: stage produced no MechanicsPlan"]}
    return {"plan": plan}


def _route_after_planner(state: PipelineState) -> str:
    plan = state.get("plan")
    if plan is not None and not plan.feasible:
        return "finalize"
    return "coder"


def _coder_node(state: PipelineState) -> dict[str, Any]:
    """Run the effect-coding stage; a missing plan degrades to a stub plan."""
    intent = _working_intent(state)
    plan = state.get("plan") or MechanicsPlan(strategy=intent.summary)
    prompt = build_coder_prompt(intent, plan, state=state.get("game_state"), actor_id=state.get("actor_id"))
    budget, max_steps = _node_budget(state, "coder")
    if budget <= 0:
        return {"coder_prompt": prompt, "stage_errors": ["coder: pipeline deadline exhausted"]}
    if intent.complexity == "trivial":
        max_steps = min(max_steps, TRIVIAL_CODER_MAX_STEPS)
    draft = run_stage(
        prompt,
        f"Implement the planned effect for the card titled {state.get('title', '')!r} and produce the JSON result.",
        _stage_tools("coder", state),
        _stage_model(state, "coder"),
        InterpretResult,
        timeout=budget,
        max_steps=max_steps,
        forced_call_timeout=_forced_timeout(state),
        config=state.get("config"),
        parse=_parse_coder,
    )
    if draft is None:
        return {"coder_prompt": prompt, "stage_errors": ["coder: stage produced no effect"]}
    return {"coder_prompt": prompt, "draft": draft}


def _validate_repair_node(state: PipelineState) -> dict[str, Any]:
    """Sandbox-validate + dry-run the coder's effect, with ONE repair call.

    Exactly :func:`agent.stage_runner._validate_or_repair_effect` semantics (the
    same machinery ``run_agent`` wires through its parse hook): a validation or
    dry-run failure triggers one tools-disabled repair call against the CODER's
    system prompt; a second failure strips the effect to ``verdict="invalid"``.
    The failing draft is validated exactly once (the error is threaded through)
    and the repair call is clamped to the pipeline deadline — with no time left
    the effect is stripped without a repair attempt.
    """
    draft = state.get("draft")
    if draft is None:
        return {}
    try:
        error = stage_runner._effect_validation_error(
            draft, state.get("game_state"), state.get("actor_id"), state.get("card_id")
        )
        if error is None:
            return {"draft": draft}
        repair_timeout = _forced_timeout(state)
        deadline = state.get("deadline")
        if deadline is not None:
            repair_timeout = min(repair_timeout, deadline - time.monotonic())
        if repair_timeout <= 0:
            logger.warning("pipeline deadline exhausted; stripping invalid effect without repair")
            stripped = draft.model_copy(update={"plan": None, "program": None, "snippet": None, "verdict": "invalid"})
            return {"draft": stripped, "stage_errors": ["validate_repair: pipeline deadline exhausted"]}
        stage_model = _stage_model(state, "coder")
        chat_model = stage_model if stage_model is not None else get_chat_model()
        validated = stage_runner._validate_or_repair_effect(
            draft,
            chat_model,
            state.get("coder_prompt", ""),
            state.get("title", ""),
            state.get("game_state"),
            state.get("actor_id"),
            state.get("card_id"),
            repair_timeout,
            _parse_coder,
            precomputed_error=error,
        )
    except Exception:  # noqa: BLE001 — validation must never escape; strip the effect instead
        logger.exception("effect validation/repair failed; stripping effect")
        validated = draft.model_copy(update={"plan": None, "program": None, "snippet": None, "verdict": "invalid"})
    return {"draft": validated}


def _finalize_node(state: PipelineState) -> dict[str, Any]:
    """Assemble the final InterpretResult: coder effect + INTENT voice.

    The coder's JSON has no ``comment`` key — the persona spoke once, in the
    intent stage, and its ``comment``/``persona_action`` are carried onto every
    result that has an intent behind it, including total coder failures (a real
    remark beats the canned fallback lines).
    """
    intent = state.get("intent")
    if intent is None:
        return {
            "result": runtime._fallback_result(
                comment="A card so mysterious even I blinked. Nothing happens.",
                note="Uninterpretable card: no effect applied.",
            )
        }

    if intent.ambiguity == "undecipherable" and intent.persona_action == "do_nothing":
        return {
            "result": InterpretResult(
                verdict="invalid",
                comment=intent.comment,
                persona_action=intent.persona_action,
            )
        }

    plan = state.get("plan")
    if plan is not None and not plan.feasible:
        note = f"Cannot implement this card's effect: {_working_intent(state).summary or state.get('title', '')}"
        if plan.infeasible_reason:
            note += f" (reason: {plan.infeasible_reason})"
        return {
            "result": InterpretResult(
                program=EffectProgram(ops=[CustomNoteOp(note=note)]),
                verdict="invalid",
                comment=intent.comment,
                persona_action=intent.persona_action,
            )
        }

    draft = state.get("draft")
    if draft is None:
        fallback = runtime._fallback_result(
            comment=intent.comment or "Something broke on my end. Your card is legally innocent.",
            note="Interpretation error: no effect applied.",
            persona_action=intent.persona_action,
        )
        return {"result": fallback}

    return {"result": draft.model_copy(update={"comment": intent.comment, "persona_action": intent.persona_action})}


def build_interpret_graph():
    """Compile the interpretation StateGraph.

    Topology: START -> intent -> (planner | finalize) -> (coder | finalize)
    -> validate_repair -> finalize -> END, with the branches decided by
    conditional edges on the intent/planner artifacts.
    """
    graph = StateGraph(PipelineState)
    graph.add_node("intent", _intent_node)
    graph.add_node("planner", _planner_node)
    graph.add_node("coder", _coder_node)
    graph.add_node("validate_repair", _validate_repair_node)
    graph.add_node("finalize", _finalize_node)

    graph.add_edge(START, "intent")
    graph.add_conditional_edges("intent", _route_after_intent, {"planner": "planner", "finalize": "finalize"})
    graph.add_conditional_edges("planner", _route_after_planner, {"coder": "coder", "finalize": "finalize"})
    graph.add_edge("coder", "validate_repair")
    graph.add_edge("validate_repair", "finalize")
    graph.add_edge("finalize", END)
    return graph.compile()


_GRAPH: Any = None


def _compiled_graph():
    global _GRAPH
    if _GRAPH is None:
        _GRAPH = build_interpret_graph()
    return _GRAPH


def run_pipeline(
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
    allow_persistent_tools: bool = False,
    config: dict[str, Any] | None = None,
) -> InterpretResult:
    """Interpret one card through the three-agent pipeline. Never hangs, never raises.

    Same parameter surface as :func:`agent.runtime.run_agent`. ``timeout`` is
    the PIPELINE-wide wall-clock ceiling (default
    :data:`agent.runtime.AGENT_TIMEOUT_SECONDS`): each node's budget is the
    smaller of its stage default and the time left before the deadline, so a
    slow early stage shrinks — and can zero out — the later ones. An explicit
    ``tools`` list is authoritative and bound to ALL THREE stages unchanged
    (see :func:`_stage_tools`). An explicit ``model`` is likewise authoritative
    for all three stages; with ``model=None`` each stage honours its own
    ``Settings.<stage>_agent_model`` override (see :func:`_stage_model`).

    Returns a well-formed :class:`InterpretResult` on every branch; on total
    failure (including anything escaping the graph) a bounded fallback with
    ``verdict="invalid"`` and ``agent_error=True``.
    """
    total_timeout = runtime.AGENT_TIMEOUT_SECONDS if timeout is None else timeout
    forced = runtime.FORCED_FINAL_CALL_TIMEOUT_SECONDS if forced_call_timeout is None else forced_call_timeout

    runtime._configure_langsmith()

    settings = get_settings()
    if card_art is not None and not settings.vision_enabled:
        card_art = None

    author_fallbacks = 0
    if state is not None and creator_id:
        try:
            author_fallbacks = fallback_counts(state).get(creator_id, 0)
        except Exception:  # noqa: BLE001 — dict snapshots lack .players; help mode is best-effort
            author_fallbacks = 0
    threshold = settings.struggling_author_threshold
    struggling_author = bool(threshold) and author_fallbacks >= threshold

    initial: PipelineState = {
        "title": title,
        "description": description,
        "card_art": card_art,
        "actor_id": actor_id,
        "creator_id": creator_id,
        "card_id": card_id,
        "game_state": state,
        "allow_persistent_tools": allow_persistent_tools,
        "model": model,
        "config": config,
        "tools": tools,
        "struggling_author": struggling_author,
        "author_fallbacks": author_fallbacks,
        "budgets": _stage_budgets(timeout, max_tool_calls, forced),
        "deadline": time.monotonic() + total_timeout,
        "stage_errors": [],
    }

    try:
        out = _compiled_graph().invoke(initial)
        result = out.get("result")
    except Exception:  # noqa: BLE001 — the pipeline must never raise to its caller
        logger.exception("interpret pipeline escaped; returning bounded fallback")
        result = None

    if isinstance(result, InterpretResult):
        return result
    return runtime._fallback_result(
        comment="Something broke on my end. Your card is legally innocent.",
        note="Interpretation error: no effect applied.",
    )
