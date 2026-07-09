"""Tests for the Tavily-backed search node (Tavily fully mocked; no network)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import tbwc.agent.nodes as nodes
from tbwc.agent.nodes import search


def _state() -> dict:
    return {
        "card_draft": {"title": "Uno", "description": "Play like the game Uno."},
        "search_notes": "intent [web_search=yes]",
    }


def test_search_appends_results_summary() -> None:
    fake_tool = MagicMock()
    fake_tool.invoke.return_value = [{"content": "Uno is a shedding-type card game."}]
    nodes._get_tavily_tool.cache_clear()
    with patch("tbwc.agent.nodes._get_tavily_tool", return_value=fake_tool):
        out = search(_state())
    assert "web_search_results" in out["search_notes"]
    assert "shedding" in out["search_notes"]
    assert out["search_notes"].startswith("intent [web_search=yes]")


def test_search_handles_string_result() -> None:
    fake_tool = MagicMock()
    fake_tool.invoke.return_value = "A plain string answer."
    with patch("tbwc.agent.nodes._get_tavily_tool", return_value=fake_tool):
        out = search(_state())
    assert "plain string answer" in out["search_notes"].lower() or "web_search_results" in out["search_notes"]


def test_search_non_fatal_on_error() -> None:
    fake_tool = MagicMock()
    fake_tool.invoke.side_effect = RuntimeError("no api key")
    with patch("tbwc.agent.nodes._get_tavily_tool", return_value=fake_tool):
        out = search(_state())
    assert "unavailable" in out["search_notes"]
    # still a string, graph can continue
    assert isinstance(out["search_notes"], str)


def test_search_preserves_existing_notes() -> None:
    fake_tool = MagicMock()
    fake_tool.invoke.return_value = []
    with patch("tbwc.agent.nodes._get_tavily_tool", return_value=fake_tool):
        out = search(_state())
    assert out["search_notes"].startswith("intent [web_search=yes]")
