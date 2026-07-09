"""Tests for tbwc.agent.state."""

from __future__ import annotations

from tbwc.agent.state import CardDraft, InterpretState


def test_interpret_state_instantiates() -> None:
    state: InterpretState = {"card_draft": {"title": "X", "description": "Y"}, "attempts": 0}
    assert state["card_draft"]["title"] == "X"
    assert state["attempts"] == 0


def test_card_draft_keys() -> None:
    draft: CardDraft = {"title": "Extra Turn", "description": "Take another turn."}
    assert set(draft.keys()) == {"title", "description"}


def test_partial_state_allowed() -> None:
    # total=False -> a state with only some keys is valid at runtime
    state: InterpretState = {"attempts": 2}
    assert state["attempts"] == 2
