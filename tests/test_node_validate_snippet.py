"""Tests for the validate_snippet node and route_after_validate edge."""

from __future__ import annotations

from agent.nodes import route_after_validate, validate_snippet_node
from agent.schemas import SnippetEffect

GOOD_SNIPPET = SnippetEffect(
    code="def apply(state, ctx):\n    state.scores[ctx['player_id']] = state.scores.get(ctx['player_id'], 0) + 1",
    explanation="Add 1 point.",
)
BAD_SNIPPET = SnippetEffect(code="import os\ndef apply(state, ctx): pass", explanation="Has import.")


def test_valid_snippet_sets_snippet_valid_true() -> None:
    result = validate_snippet_node({"snippet": GOOD_SNIPPET, "snippet_attempts": 1})
    assert result == {"snippet_valid": True}


def test_invalid_snippet_sets_valid_false_and_appends_error() -> None:
    result = validate_snippet_node({"snippet": BAD_SNIPPET, "snippet_attempts": 1})
    assert result.get("snippet_valid") is False
    assert "[validate_error:" in result.get("search_notes", "")


def test_missing_snippet_sets_valid_false_and_appends_error() -> None:
    result = validate_snippet_node({"snippet_attempts": 1})
    assert result.get("snippet_valid") is False
    assert "[validate_error:" in result.get("search_notes", "")


def test_route_valid_snippet_goes_to_judge() -> None:
    assert route_after_validate({"snippet_valid": True}) == "judge"


def test_route_invalid_retries_if_under_limit() -> None:
    assert route_after_validate({"snippet_valid": False, "snippet_attempts": 1}) == "gen_snippet"


def test_route_invalid_proceeds_at_limit() -> None:
    assert route_after_validate({"snippet_valid": False, "snippet_attempts": 3}) == "judge"


def test_route_valid_ignores_prior_validate_error_note() -> None:
    # A later valid snippet must route to judge even if an earlier error note lingers.
    state = {"snippet_valid": True, "snippet_attempts": 2, "search_notes": "x [validate_error: import]"}
    assert route_after_validate(state) == "judge"
