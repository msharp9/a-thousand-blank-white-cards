"""Tests for the validate_snippet node and route_after_validate edge."""

from __future__ import annotations

from tbwc.agent.nodes import route_after_validate, validate_snippet_node
from tbwc.agent.schemas import SnippetEffect

GOOD_SNIPPET = SnippetEffect(
    code="def apply(state, ctx):\n    state.scores[ctx['player_id']] = state.scores.get(ctx['player_id'], 0) + 1",
    explanation="Add 1 point.",
)
BAD_SNIPPET = SnippetEffect(code="import os\ndef apply(state, ctx): pass", explanation="Has import.")


def test_valid_snippet_passes_through() -> None:
    assert validate_snippet_node({"snippet": GOOD_SNIPPET, "attempts": 1}) == {}


def test_invalid_snippet_appends_error() -> None:
    result = validate_snippet_node({"snippet": BAD_SNIPPET, "attempts": 1})
    assert "[validate_error:" in result.get("search_notes", "")


def test_missing_snippet_appends_error() -> None:
    result = validate_snippet_node({"attempts": 1})
    assert "[validate_error:" in result.get("search_notes", "")


def test_route_on_error_retries_if_under_limit() -> None:
    state = {"search_notes": "notes [validate_error: import]", "attempts": 1}
    assert route_after_validate(state) == "gen_snippet"


def test_route_on_error_proceeds_at_limit() -> None:
    state = {"search_notes": "notes [validate_error: import]", "attempts": 3}
    assert route_after_validate(state) == "judge"


def test_route_no_error_proceeds() -> None:
    assert route_after_validate({"search_notes": "notes", "attempts": 1}) == "judge"
