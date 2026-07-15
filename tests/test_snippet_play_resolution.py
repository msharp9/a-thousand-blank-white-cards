"""Bead bsv — wire the sandbox pipeline into Room._resolve_program's serving path.

Before this bead, a snippet-only interpretation (``result.snippet`` set, no usable
``result.program``) was broadcast/stored but never executed: the play always fell
back to a bare CustomNoteOp ("[note] Played X (no mechanical effect)"). These tests
assert the snippet now runs through the same sandbox pipeline persistent hooks use
(``execute_snippet`` -> ``apply_snippet_diff``) and mutates state via the real
reducers, that a sandbox failure falls back cleanly (a friendly "no mechanical effect"
note, card discarded, mechanical_status "fallback") WITHOUT leaking the raw error into
the shared player log, and that the feature flag preserves today's behavior when off.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

from config import Settings
from agent.contract import InterpretResult, SnippetEffect
from models.effects import DrawCardsOp, EffectProgram
from models.game_state import HookSpec
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
    r._has_drawn = True
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


def test_failing_snippet_falls_back_without_leaking_error() -> None:
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
    # The raw failure never reaches the shared player log...
    assert not any("[snippet error]" in line for line in room.state.log)
    assert not any("boom" in line for line in room.state.log)
    # ...but the play still resolves (never a silent no-op) and the technical
    # reason is captured privately on the card for dev/triage.
    assert any("no mechanical effect" in line for line in room.state.log)
    assert room.state.cards["c2"]["mechanical_status"] == "fallback"
    assert room.state.cards["c2"]["mechanical_reason"]
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


def test_choice_target_snippet_diff_falls_back_instead_of_crashing() -> None:
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
    assert not any("[snippet error]" in line for line in room.state.log)
    assert room.state.cards["c4"]["mechanical_status"] == "fallback"


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


def test_failing_suffix_rolls_back_prefix_but_consumes_card() -> None:
    card = _snippet_card("c6")
    room = _room_with_card(card)
    room.state = room.state.model_copy(update={"deck": ["d1", "d2", "d3"]})
    agent_result = InterpretResult(
        program=EffectProgram(ops=[DrawCardsOp(target="self", amount=2)]),
        snippet=SnippetEffect(code=BROKEN_SNIPPET, explanation="fails after drawing"),
        verdict="ok",
        comment="Nope.",
    )

    with patch("agent.runtime.run_agent", return_value=agent_result):
        asyncio.run(room.handle_action("p1", PlayMsg(card_id="c6")))

    # The failed plan's own draws rolled back; the only deck movement is the
    # NEXT player's turn-start auto-draw (the play consumed the turn).
    assert room.state.deck == ["d2", "d3"]
    assert room.state.get_player("p1").hand == []
    assert "d1" in room.state.get_player("p2").hand
    assert "c6" in room.state.discard
    assert any("no mechanical effect" in line for line in room.state.log)
    draws = [event for event in room.state.history_events if event.kind == "draw"]
    assert [(event.target_player_ids, event.amount) for event in draws] == [(["p2"], 1)]
    assert len([event for event in room.state.history_events if event.kind == "play"]) == 1


def test_snippet_score_change_uses_room_hooks() -> None:
    card = _snippet_card("c7")
    room = _room_with_card(card)
    room.state = room.state.model_copy(
        update={
            "hooks": [
                HookSpec(
                    id="score-hook",
                    source_card_id="source",
                    event="on_score_change",
                    code="def apply(state, ctx):\n    state.add_points('id:p2', 1)\n",
                )
            ]
        }
    )
    agent_result = InterpretResult(
        snippet=SnippetEffect(code=AWARD_SNIPPET, explanation="award"),
        verdict="ok",
        comment="Points.",
    )

    with patch("agent.runtime.run_agent", return_value=agent_result):
        asyncio.run(room.handle_action("p1", PlayMsg(card_id="c7")))

    assert room.state.get_player("p1").score == 7
    assert room.state.get_player("p2").score == 1


def test_snippet_end_game_is_immediate() -> None:
    card = _snippet_card("c8")
    room = _room_with_card(card)
    room.state = room.state.model_copy(update={"deck": ["d1", "d2"]})
    agent_result = InterpretResult(
        snippet=SnippetEffect(
            code="def apply(state, ctx):\n    state.end_game(winner='self')\n",
            explanation="end now",
        ),
        verdict="ok",
        comment="Done.",
    )

    with patch("agent.runtime.run_agent", return_value=agent_result):
        asyncio.run(room.handle_action("p1", PlayMsg(card_id="c8")))

    assert room.state.phase == "results"
    assert room.state.winner_ids == ["p1"]


def test_hybrid_plan_persists_and_replays_without_agent() -> None:
    card = _snippet_card("c9")
    first = _room_with_card(card)
    first.state = first.state.model_copy(update={"deck": ["d1", "d2"]})
    scorer = "def apply(state, ctx):\n    state.add_points('self', len(state.my_hand()))\n"
    agent_result = InterpretResult(
        program=EffectProgram(ops=[DrawCardsOp(target="self", amount=2)]),
        snippet=SnippetEffect(code=scorer, explanation="score post-draw hand"),
        verdict="ok",
        comment="Count them.",
    )

    with patch("agent.runtime.run_agent", return_value=agent_result):
        asyncio.run(first.handle_action("p1", PlayMsg(card_id="c9")))

    stored = first.state.cards["c9"]
    assert [step["kind"] for step in stored["canonical"]["steps"]] == ["ops", "snippet"]
    assert first.state.get_player("p1").score == 2

    second = _room_with_card(stored)
    second.state = second.state.model_copy(update={"deck": ["e1", "e2"]})
    with patch("agent.runtime.run_agent", side_effect=AssertionError("agent should not run")) as run_agent:
        asyncio.run(second.handle_action("p1", PlayMsg(card_id="c9")))

    run_agent.assert_not_called()
    assert second.state.get_player("p1").score == 2


def test_hybrid_plan_emits_on_play_once() -> None:
    card = _snippet_card("c10")
    room = _room_with_card(card)
    room.state = room.state.model_copy(
        update={
            "deck": ["d1"],
            "hooks": [
                HookSpec(
                    id="play-hook",
                    source_card_id="source",
                    event="on_play",
                    code="def apply(state, ctx):\n    state.add_points('id:p2', 1)\n",
                )
            ],
        }
    )
    agent_result = InterpretResult(
        program=EffectProgram(ops=[DrawCardsOp(target="self", amount=1)]),
        snippet=SnippetEffect(code=AWARD_SNIPPET, explanation="award"),
        verdict="ok",
        comment="Once.",
    )

    with patch("agent.runtime.run_agent", return_value=agent_result):
        asyncio.run(room.handle_action("p1", PlayMsg(card_id="c10")))

    assert room.state.get_player("p2").score == 1
