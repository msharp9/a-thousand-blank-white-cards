"""Bead 70n.5 — deterministic play resolution (ops→apply_effect, LLM fallback, no silent no-op).

``Room._handle_play`` resolves a played card to an EffectProgram in this order:
  1. compiled ops (deterministic, no LLM) — a card with structured canonical ops,
  2. best-effort LLM interpretation for free-text cards,
  3. a CustomNoteOp fallback so a play NEVER silently no-ops.

These tests assert the compiled path never touches the agent, the LLM path is used
only when there are no compiled ops, and a card that resolves to nothing still
produces a log line and advances the turn.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

from models.effects import AddPointsOp, EffectProgram
from models.ws_messages import PlayMsg
from rooms.room import Room


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


def _gain_card(card_id: str, amount: int = 5) -> dict:
    """A structured 'gain N to self' gold card that compiles WITHOUT the LLM."""
    return {
        "id": card_id,
        "title": f"Gain {amount}",
        "description": f"Gain {amount} points.",
        "canonical": {
            "timing": "immediate",
            "target": "self",
            "placement": "self",
            "ops": [{"op": "add_points", "args": {"amount": amount, "target": "self"}}],
        },
    }


def test_compiled_card_applies_without_calling_the_llm() -> None:
    # A structured card must resolve deterministically — interpret_card is never
    # called, and the score changes by the compiled amount.
    room = _room_with_card(_gain_card("c1", 5))
    with patch("agent.graph.interpret_card", side_effect=AssertionError("LLM must not be called")) as spy:
        asyncio.run(room.handle_action("p1", PlayMsg(card_id="c1")))
    spy.assert_not_called()
    assert room.state.get_player("p1").score == 5
    assert "c1" not in room.state.get_player("p1").hand
    assert "c1" in room.state.discard


def test_free_text_card_falls_back_to_llm() -> None:
    # A card with NO compilable ops uses the best-effort LLM path.
    card = {"id": "c2", "title": "Mystery", "description": "Something happens.", "creator_id": "p1"}
    room = _room_with_card(card)
    llm_program = {
        "program": EffectProgram(ops=[AddPointsOp(target="self", amount=3)]),
        "snippet": None,
        "verdict": "ok",
    }
    with patch("agent.graph.interpret_card", return_value=llm_program) as spy:
        asyncio.run(room.handle_action("p1", PlayMsg(card_id="c2")))
    spy.assert_called_once()
    assert room.state.get_player("p1").score == 3
    assert "c2" in room.state.discard


def test_llm_failure_falls_back_to_custom_note_and_advances() -> None:
    # If the LLM raises, the play still resolves (CustomNoteOp), the card leaves
    # the hand, and the turn advances — never a silent no-op / stuck turn.
    card = {"id": "c3", "title": "Chaos", "description": "Who knows.", "creator_id": "p1"}
    room = _room_with_card(card)
    start_turn = room.state.turn_index
    with patch("agent.graph.interpret_card", side_effect=RuntimeError("boom")):
        asyncio.run(room.handle_action("p1", PlayMsg(card_id="c3")))
    # No score change, but the card was consumed and a log line was recorded.
    assert room.state.get_player("p1").score == 0
    assert "c3" in room.state.discard
    assert any("no mechanical effect" in line for line in room.state.log)
    assert room.state.turn_index != start_turn


def test_llm_invalid_verdict_falls_back_to_custom_note() -> None:
    # An 'invalid' verdict (no usable program) also hits the deterministic fallback.
    card = {"id": "c4", "title": "Nonsense", "description": "???", "creator_id": "p1"}
    room = _room_with_card(card)
    with patch(
        "agent.graph.interpret_card",
        return_value={"program": None, "snippet": None, "verdict": "invalid"},
    ):
        asyncio.run(room.handle_action("p1", PlayMsg(card_id="c4")))
    assert "c4" in room.state.discard
    assert any("no mechanical effect" in line for line in room.state.log)


def test_compiled_card_targeting_all_others() -> None:
    # A compiled multi-target program applies deterministically to all others.
    card = {
        "id": "c5",
        "title": "Everyone Else Loses 2",
        "description": "All other players lose 2 points.",
        "canonical": {
            "timing": "immediate",
            "target": "all",
            "placement": "self",
            "ops": [{"op": "subtract_points", "args": {"amount": 2, "target": "all_others"}}],
        },
    }
    room = _room_with_card(card)
    with patch("agent.graph.interpret_card", side_effect=AssertionError("no LLM")):
        asyncio.run(room.handle_action("p1", PlayMsg(card_id="c5")))
    assert room.state.get_player("p1").score == 0
    assert room.state.get_player("p2").score == -2
