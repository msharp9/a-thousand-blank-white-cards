"""Bead bsv — wire the sandbox pipeline into Room._resolve_program's serving path.

Before this bead, a snippet-only interpretation (``result.snippet`` set, no usable
``result.program``) was broadcast/stored but never executed: the play always fell
back to a bare CustomNoteOp ("[note] Played X (no mechanical effect)"). These tests
assert the snippet now runs through the same sandbox pipeline persistent hooks use
(``execute_snippet`` -> ``apply_snippet_diff``) and mutates state via the real
reducers, that a sandbox failure logs a visible "[snippet error]" line and leaves
state unchanged (falling back to the note), and that the feature flag preserves
today's behavior exactly when off.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

from config import Settings
from agent.contract import InterpretResult, SnippetEffect
from models.ws_messages import PlayMsg
from board.rooms.room import Room

AWARD_SNIPPET = "def apply(state, ctx):\n    state.add_points('self', 7)\n"
BROKEN_SNIPPET = "def apply(state, ctx):\n    raise RuntimeError('boom')\n"


def _room_with_card(card: dict, *, hand_owner: str = "p1") -> Room:
    """Two-player playing room with ``card`` seeded into the owner's hand."""
    r = Room("ABCDEF")
    r.add_player("p1", "Alice")
    r.add_player("p2", "Bob")
    r.state = r.state.model_copy(update={"phase": "playing"})
    new_players = [
        p.model_copy(update={"hand": [card["id"]]}) if p.id == hand_owner else p.model_copy(update={"hand": ["other"]})
        for p in r.state.players
    ]
    r.state = r.state.model_copy(update={"cards": {card["id"]: card}, "players": new_players})
    r.connections.connect("p1", AsyncMock())
    r.connections.connect("p2", AsyncMock())
    return r


def _snippet_card(card_id: str) -> dict:
    """A free-text card with no compilable ops (forces the agent path)."""
    return {"id": card_id, "title": "Chess", "description": "Draw 2, then score per card in hand.", "creator_id": "p1"}


def test_snippet_only_interpretation_mutates_state_via_sandbox() -> None:
    card = _snippet_card("c1")
    room = _room_with_card(card)
    agent_result = InterpretResult(
        program=None,
        snippet=SnippetEffect(code=AWARD_SNIPPET, explanation="award the actor 7 points"),
        verdict="ok",
        comment="Sure, why not.",
    )
    with patch("agent.runtime.run_agent", return_value=agent_result):
        asyncio.run(room.handle_action("p1", PlayMsg(card_id="c1")))

    assert room.state.get_player("p1").score == 7
    assert "c1" in room.state.discard
    assert not any("no mechanical effect" in line for line in room.state.log)


def test_failing_snippet_logs_error_and_leaves_state_unchanged() -> None:
    card = _snippet_card("c2")
    room = _room_with_card(card)
    agent_result = InterpretResult(
        program=None,
        snippet=SnippetEffect(code=BROKEN_SNIPPET, explanation="deliberately broken"),
        verdict="ok",
        comment="Uh oh.",
    )
    with patch("agent.runtime.run_agent", return_value=agent_result):
        asyncio.run(room.handle_action("p1", PlayMsg(card_id="c2")))

    assert room.state.get_player("p1").score == 0
    assert room.state.get_player("p2").score == 0
    assert any("[snippet error] Chess" in line for line in room.state.log)
    # No mechanical effect: the play still resolves (never a silent no-op).
    assert any("no mechanical effect" in line for line in room.state.log)
    assert "c2" in room.state.discard


def test_snippet_execution_disabled_preserves_current_behavior() -> None:
    card = _snippet_card("c3")
    room = _room_with_card(card)
    agent_result = InterpretResult(
        program=None,
        snippet=SnippetEffect(code=AWARD_SNIPPET, explanation="award the actor 7 points"),
        verdict="ok",
        comment="Nope.",
    )
    disabled = Settings(_env_file=None, snippet_execution_enabled=False)  # type: ignore[call-arg]
    with patch("agent.runtime.run_agent", return_value=agent_result):
        with patch("config.get_settings", return_value=disabled):
            with patch("engine.sandbox.runner.execute_snippet") as spy:
                asyncio.run(room.handle_action("p1", PlayMsg(card_id="c3")))

    spy.assert_not_called()
    assert room.state.get_player("p1").score == 0
    assert any("no mechanical effect" in line for line in room.state.log)
    assert "c3" in room.state.discard


def test_choice_target_snippet_diff_logs_error_instead_of_crashing() -> None:
    card = _snippet_card("c4")
    room = _room_with_card(card)
    agent_result = InterpretResult(
        program=None,
        snippet=SnippetEffect(
            code="def apply(state, ctx):\n    state.skip('target_player')\n", explanation="skip someone"
        ),
        verdict="ok",
        comment="Pick a victim.",
    )
    score_before = room.state.get_player("p1").score
    with patch("agent.runtime.run_agent", return_value=agent_result):
        asyncio.run(room.handle_action("p1", PlayMsg(card_id="c4")))

    assert room.state.get_player("p1").score == score_before
    assert any("[snippet error]" in line for line in room.state.log)


def test_non_ok_verdict_never_executes_snippet() -> None:
    card = _snippet_card("c5")
    room = _room_with_card(card)
    agent_result = InterpretResult(
        program=None,
        snippet=SnippetEffect(code=AWARD_SNIPPET, explanation="award"),
        verdict="invalid",
        comment="Nope.",
    )
    with patch("agent.runtime.run_agent", return_value=agent_result):
        with patch("engine.sandbox.runner.execute_snippet") as exec_mock:
            asyncio.run(room.handle_action("p1", PlayMsg(card_id="c5")))

    exec_mock.assert_not_called()
    assert room.state.get_player("p1").score == 0
