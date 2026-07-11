"""Bead 70n.4 — a played card must leave the hand and land in the correct zone.

These tests drive ``Room._handle_play`` (via ``handle_action``) with the agent's
``run_agent`` stubbed so no real LLM runs, and assert the played card is
removed from the actor's hand and appended to the zone derived from its canonical
placement/timing:

- immediate point card / no canonical → discard
- placement="self" + timing="modifier" → the actor's in_play zone
- placement="center" → the shared center zone (house_rules)
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

from agent.contract import InterpretResult
from models.effects import AddPointsOp, EffectProgram
from models.ws_messages import PlayMsg
from board.rooms.room import Room


def _room_with_card(card: dict) -> Room:
    """Two-player playing room with ``card`` seeded into p1's hand."""
    r = Room("ABCDEF")
    r.add_player("p1", "Alice")
    r.add_player("p2", "Bob")
    r.state = r.state.model_copy(update={"phase": "playing"})
    # Seed the card into the registry and into p1's hand (p2 gets an untouched hand).
    new_players = [
        p.model_copy(update={"hand": [card["id"]]}) if p.id == "p1" else p.model_copy(update={"hand": ["other"]})
        for p in r.state.players
    ]
    r.state = r.state.model_copy(update={"cards": {card["id"]: card}, "players": new_players})
    r.connections.connect("p1", AsyncMock())
    r.connections.connect("p2", AsyncMock())
    return r


_OK_PROGRAM = InterpretResult(
    program=EffectProgram(ops=[AddPointsOp(target="self", amount=5)]),
    snippet=None,
    verdict="ok",
)


def _play(room: Room, card_id: str) -> None:
    with patch("agent.runtime.run_agent", return_value=_OK_PROGRAM):
        asyncio.run(room.handle_action("p1", PlayMsg(card_id=card_id)))


def test_immediate_point_card_goes_to_discard() -> None:
    card = {"id": "c1", "title": "Gain 5", "description": "Gain 5 points."}
    room = _room_with_card(card)
    _play(room, "c1")

    assert "c1" not in room.state.get_player("p1").hand
    assert "c1" in room.state.discard
    assert "c1" not in room.state.cards_in_play()
    assert "c1" not in room.state.center_cards()
    # registry retains the card; the other player's hand is untouched.
    assert "c1" in room.state.cards
    assert room.state.get_player("p2").hand == ["other"]


def test_no_canonical_card_goes_to_discard() -> None:
    # A card with an explicit non-modifier canonical still discards.
    card = {
        "id": "c2",
        "title": "Gain 5",
        "description": "Gain 5 points.",
        "canonical": {"placement": "self", "timing": "immediate", "target": "self"},
    }
    room = _room_with_card(card)
    _play(room, "c2")

    assert "c2" not in room.state.get_player("p1").hand
    assert "c2" in room.state.discard
    assert "c2" in room.state.cards


def test_self_modifier_card_goes_to_in_play() -> None:
    card = {
        "id": "c3",
        "title": "Aura",
        "description": "While in play, gain a point each turn.",
        "canonical": {"placement": "self", "timing": "modifier", "target": "self"},
    }
    room = _room_with_card(card)
    _play(room, "c3")

    assert "c3" not in room.state.get_player("p1").hand
    assert "c3" in room.state.cards_in_play_for("p1")
    assert "c3" not in room.state.discard
    assert "c3" not in room.state.center_cards()
    assert "c3" in room.state.cards


def test_center_placement_card_goes_to_center() -> None:
    card = {
        "id": "c4",
        "title": "New House Rule",
        "description": "Everyone draws two.",
        "canonical": {"placement": "center", "timing": "modifier", "target": "center"},
    }
    room = _room_with_card(card)
    _play(room, "c4")

    assert "c4" not in room.state.get_player("p1").hand
    assert "c4" in room.state.center_cards()
    assert "c4" not in room.state.discard
    assert "c4" not in room.state.cards_in_play()
    assert "c4" in room.state.cards


def test_rejected_play_keeps_card_in_hand() -> None:
    # A blank with no authored title/description is rejected early: the card must
    # stay in the hand (turn not consumed).
    card = {"id": "c5", "title": "", "description": "", "blank": True}
    room = _room_with_card(card)
    with patch("agent.runtime.run_agent", return_value=_OK_PROGRAM):
        asyncio.run(room.handle_action("p1", PlayMsg(card_id="c5")))

    assert "c5" in room.state.get_player("p1").hand
    assert "c5" not in room.state.discard
