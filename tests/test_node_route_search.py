"""Tests for route_search node and should_search edge."""

from __future__ import annotations

from agent.nodes import route_search, should_search


def test_proper_noun_triggers_search() -> None:
    state = {"card_draft": {"title": "Thanos Snaps", "description": "Thanos Snap erases half the players."}}
    result = route_search(state)
    assert "[web_search=yes]" in result["search_notes"]


def test_quoted_phrase_triggers_search() -> None:
    state = {"card_draft": {"title": "Rule", "description": 'Invoke "Cunningham\'s Law" now.'}}
    result = route_search(state)
    assert "[web_search=yes]" in result["search_notes"]


def test_simple_card_skips_search() -> None:
    state = {"card_draft": {"title": "Extra turn", "description": "Take another turn."}}
    result = route_search(state)
    assert "[web_search=no]" in result["search_notes"]


def test_route_search_preserves_prior_notes() -> None:
    state = {"card_draft": {"title": "x", "description": "y"}, "search_notes": "intent summary"}
    result = route_search(state)
    assert result["search_notes"].startswith("intent summary")


def test_should_search_routes_to_search() -> None:
    assert should_search({"search_notes": "intent summary [web_search=yes]"}) == "search"


def test_should_search_routes_to_classify() -> None:
    assert should_search({"search_notes": "intent summary [web_search=no]"}) == "classify"
