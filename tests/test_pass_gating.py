"""Bead 70n.7 — pass is only allowed when the player has NO playable card.

Blanks are always playable, and any card with an effect (a compiled program) or
free text is playable, so a player holding one must play rather than pass. Pass
is offered (``can_pass`` in the snapshot, and accepted by the server) only when
the hand holds nothing playable.
"""

from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock

from models.ws_messages import PassMsg
from board.rooms.room import Room


def _playing_room(p1_hand: list[str], cards: dict) -> Room:
    r = Room("ABCDEF")
    r.add_player("p1", "Alice")
    r.add_player("p2", "Bob")
    new_players = [p.model_copy(update={"hand": p1_hand}) if p.id == "p1" else p for p in r.state.players]
    r.state = r.state.model_copy(update={"phase": "playing", "deck": [], "cards": cards, "players": new_players})
    r._has_drawn = True  # mirrors the turn-start auto-draw bookkeeping
    return r


def test_can_pass_false_when_holding_a_blank() -> None:
    room = _playing_room(["b1"], {"b1": {"id": "b1", "title": "", "description": "", "blank": True}})
    assert room._can_pass("p1") is False
    assert room.snapshot()["can_pass"] is False


def test_can_pass_false_when_holding_an_effect_card() -> None:
    card = {
        "id": "c1",
        "title": "Gain 5",
        "description": "Gain 5 points.",
        "canonical": {
            "timing": "immediate",
            "target": "self",
            "placement": "self",
            "ops": [{"op": "add_points", "args": {"amount": 5, "target": "self"}}],
        },
    }
    room = _playing_room(["c1"], {"c1": card})
    assert room._can_pass("p1") is False


def test_can_pass_true_with_empty_hand() -> None:
    room = _playing_room([], {})
    assert room._can_pass("p1") is True
    assert room.snapshot()["can_pass"] is True


def test_can_pass_true_with_only_inert_card() -> None:
    # A card with no canonical/ops AND no description is inert → not playable.
    room = _playing_room(["x1"], {"x1": {"id": "x1", "title": "Relic", "description": ""}})
    assert room._can_pass("p1") is True


def test_pass_rejected_when_holding_playable_card() -> None:
    room = _playing_room(["b1"], {"b1": {"id": "b1", "title": "", "description": "", "blank": True}})
    ws1 = AsyncMock()
    room.connections.connect("p1", ws1)
    asyncio.run(room.handle_action("p1", PassMsg()))
    sent = [json.loads(c.args[0])["type"] for c in ws1.send_text.call_args_list]
    assert "error" in sent
    # Turn did not advance.
    assert room.state.turn_index == 0


def test_pass_allowed_with_empty_hand_advances_turn() -> None:
    room = _playing_room([], {})
    room._deck_exhausted = False
    # add a filler card into deck so pass advances rather than ending the game
    room.state = room.state.model_copy(
        update={"deck": ["d1"], "cards": {"d1": {"id": "d1", "title": "x", "description": ""}}}
    )
    room._has_drawn = True
    ws1 = AsyncMock()
    room.connections.connect("p1", ws1)
    asyncio.run(room.handle_action("p1", PassMsg()))
    assert room.state.turn_index == 1


def test_can_pass_false_for_non_active_player() -> None:
    room = _playing_room([], {})
    assert room._can_pass("p2") is False
