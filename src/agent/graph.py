"""agent.graph — assemble and compile the LangGraph interpretation StateGraph.

Exposes the module-level `graph` (a compiled CompiledStateGraph). Imported by the
rooms API and the eval harness via `from agent.graph import graph`.
"""

from __future__ import annotations

from typing import TypedDict

from langgraph.graph import END, START, StateGraph

from agent.nodes import (
    classify,
    emit_ops,
    gen_snippet,
    judge,
    reason,
    retrieve,
    route_after_classify,
    route_after_judge,
    route_after_validate,
    route_search,
    search,
    should_search,
    validate_snippet_node,
)
from agent.state import InterpretState


class AgentConfig(TypedDict, total=False):
    """Configurable keys accepted by the interpretation graph."""

    retriever_mode: str  # "dense" | "advanced"
    few_shot_exemplars: bool  # inject retrieved exemplars into emit_ops (default True)


def build_graph() -> StateGraph:
    """Build (but do not compile) the interpretation StateGraph."""
    try:
        builder = StateGraph(InterpretState, config_schema=AgentConfig)
    except TypeError:
        builder = StateGraph(InterpretState)

    builder.add_node("reason", reason)
    builder.add_node("retrieve", retrieve)
    builder.add_node("route_search", route_search)
    builder.add_node("search", search)
    builder.add_node("classify", classify)
    builder.add_node("emit_ops", emit_ops)
    builder.add_node("gen_snippet", gen_snippet)
    builder.add_node("validate_snippet", validate_snippet_node)
    builder.add_node("judge", judge)

    builder.add_edge(START, "reason")
    builder.add_edge("reason", "retrieve")
    builder.add_edge("retrieve", "route_search")

    builder.add_conditional_edges("route_search", should_search, {"search": "search", "classify": "classify"})
    builder.add_edge("search", "classify")

    builder.add_conditional_edges(
        "classify", route_after_classify, {"emit_ops": "emit_ops", "gen_snippet": "gen_snippet"}
    )
    builder.add_edge("emit_ops", "judge")

    builder.add_edge("gen_snippet", "validate_snippet")
    builder.add_conditional_edges(
        "validate_snippet", route_after_validate, {"gen_snippet": "gen_snippet", "judge": "judge"}
    )

    builder.add_conditional_edges("judge", route_after_judge, {"classify": "classify", END: END})

    return builder


# Compiled graph imported by the rooms API and eval harness.
graph = build_graph().compile()


def interpret_card(title: str, description: str, state=None, actor_id: str | None = None) -> dict:
    """Synchronous entry point: run the interpretation graph on one card.

    This is the CANONICAL forward-looking signature. ``state`` (a GameState or
    serializable snapshot) and ``actor_id`` (str) are accepted so callers can pass
    them today; they are IGNORED for now (the old graph does not use them) and will
    be wired up by the rewritten agent (beads C1/C10) without another signature change.

    Returns a plain dict matching the :class:`agent.contract.InterpretResult` shape::

        {"program": EffectProgram | None, "snippet": SnippetEffect | None,
         "verdict": "ok" | "invalid" | "needs_choice",
         "comment": str, "persona_action": str}

    The legacy keys (``program``/``snippet``/``verdict``) are preserved exactly, so
    existing callers and ~40 test call-sites that patch this function and read only
    those keys keep working. ``comment`` defaults to ``""`` and ``persona_action`` to
    ``"none"`` (populated by the real agent later). Safe to call inside asyncio.to_thread.
    """
    final = graph.invoke({"card_draft": {"title": title, "description": description}, "attempts": 0})
    verdict_obj = final.get("verdict")
    verdict = "ok" if (verdict_obj is not None and getattr(verdict_obj, "ok", False)) else "invalid"
    return {
        "program": final.get("program"),
        "snippet": final.get("snippet"),
        "verdict": verdict,
        "comment": "",
        "persona_action": "none",
    }
