"""Tests for the search stub node."""

from __future__ import annotations

from tbwc.agent.nodes import search


def test_search_stub_returns_search_notes() -> None:
    state = {"card_draft": {"title": "Thanos", "description": "..."}, "search_notes": "intent [web_search=yes]"}
    result = search(state)
    assert "search_notes" in result
    lowered = result["search_notes"].lower()
    assert "stub" in lowered or "none" in lowered


def test_search_stub_preserves_existing_notes() -> None:
    state = {"card_draft": {"title": "X", "description": "Y"}, "search_notes": "prior notes"}
    result = search(state)
    assert "prior notes" in result["search_notes"]


def test_search_stub_handles_missing_notes() -> None:
    result = search({"card_draft": {"title": "X", "description": "Y"}})
    assert isinstance(result["search_notes"], str)
