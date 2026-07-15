"""Tests for the agent.tools aggregator (get_default_tools)."""

from __future__ import annotations

import agent.tools as tools_pkg
from agent.tools import get_default_tools


def test_default_tools_include_all_context_free_tools() -> None:
    names = {t.name for t in get_default_tools()}
    assert {
        "web_search",
        "card_rag_hybrid",
        "game_rules",
        "mtg_lookup",
        "remember_decision",
        "recall_decisions",
    } <= names


def test_default_tools_all_have_descriptions() -> None:
    for tool in get_default_tools():
        assert tool.description, f"tool {tool.name} has no description"


def test_one_bad_tool_does_not_break_the_toolbox(monkeypatch) -> None:
    """A single tool factory raising must degrade to a smaller toolbox, not crash."""

    def boom() -> object:
        raise RuntimeError("optional dep missing")

    monkeypatch.setattr(tools_pkg, "get_web_search_tool", boom, raising=False)
    # Re-import target inside the function references the module attr, so patch the
    # source module the aggregator imports from.
    import agent.tools.web_search as ws

    monkeypatch.setattr(ws, "get_web_search_tool", boom)

    names = {t.name for t in get_default_tools()}
    # web_search is dropped, the rest survive.
    assert "web_search" not in names
    assert "card_rag_hybrid" in names
