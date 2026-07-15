"""agent.tools — the toolbox bound to the single interpretation agent.

Each tool lives in its own module and is exposed as a LangChain tool object
(a ``@tool``-decorated callable or ``BaseTool`` instance). ``get_default_tools``
aggregates the ones that are safe to bind by default; tools requiring per-call
context (the live game-state / engine-introspection tools) are provided by the
caller at ``build_agent(tools=...)`` time instead.

Layering: modules here may import ``engine``, ``models``, ``config``,
``logging_config`` — never ``board``. Game state reaches state-reading tools as a
passed-in snapshot, not via a board import.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger("agent.tools")


def get_default_tools(*, allow_persistent_tools: bool = True) -> list[Any]:
    """Return the context-free tools safe to bind on every agent build.

    These are the tools whose behaviour does not depend on the specific game in
    play — web search, MTG lookup, the game-rules reference, the interpreted-card
    RAG corpus, the agent's own decision memory, and the engine-introspection
    reference (read_engine_methods). The one context-DEPENDENT tool,
    read_game_state (bead C6), closes over the live snapshot and is bound
    per-invocation by the caller (run_agent), not returned here.

    Imports are done lazily inside the function so that importing ``agent.tools``
    stays cheap and a single tool's optional dependency (e.g. langchain-tavily,
    qdrant) failing to import degrades to a smaller toolbox rather than breaking
    agent construction entirely.
    """
    tools: list[Any] = []

    from agent.tools.agent_memory import get_agent_memory_tools
    from agent.tools.card_rag_hybrid import get_card_rag_hybrid_tool
    from agent.tools.game_rules import get_game_rules_tool
    from agent.tools.mtg_lookup import get_mtg_lookup_tool
    from agent.tools.read_engine_methods import get_read_engine_methods_tool
    from agent.tools.web_search import get_web_search_tool

    factories = [
        get_web_search_tool,
        get_card_rag_hybrid_tool,
        get_game_rules_tool,
        get_mtg_lookup_tool,
        get_read_engine_methods_tool,
    ]
    for factory in factories:
        try:
            tools.append(factory())
        except Exception:  # noqa: BLE001 — a missing optional dep must not break agent build
            logger.warning("tool %s unavailable; skipping", getattr(factory, "__name__", factory))

    if allow_persistent_tools:
        try:
            tools.extend(get_agent_memory_tools())
        except Exception:  # noqa: BLE001
            logger.warning("agent_memory tools unavailable; skipping")

    return tools
