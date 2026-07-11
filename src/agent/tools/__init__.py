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

from typing import Any


def get_default_tools() -> list[Any]:
    """Return the context-free tools safe to bind on every agent build.

    Populated as the individual tool beads (C2-C5, C8) land. Each newly added
    tool module appends its tool object here. Context-dependent tools
    (read_game_state / read_engine_methods, bead C6/C7) are bound per-invocation
    by the caller, not returned here.
    """
    tools: list[Any] = []
    return tools
