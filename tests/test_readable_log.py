"""Bead 70n.20 (backend half) — human-readable play log lines with score deltas.

The play log used to be a raw ``Played <card_id>``; it now reads
``<name> played <title> (<name> +/-N, …)`` so players can follow the game.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

from tbwc.models.effects import EffectProgram
from tbwc.models.ws_messages import PlayMsg
from tbwc.rooms.room import Room


def _playing_room(card: dict) -> Room:
    r = Room("ABCDEF")
    r.add_player("p1", "Alice")
    r.add_player("p2", "Bob")
    players = [r.state.players[0].model_copy(update={"hand": [card["id"]]}), r.state.players[1]]
    r.state = r.state.model_copy(
        update={"phase": "playing", "deck": ["d1", "d2"], "cards": {card["id"]: card}, "players": players}
    )
    r._has_drawn = True
    r.connections.connect("p1", AsyncMock())
    r.connections.connect("p2", AsyncMock())
    return r


def test_play_log_uses_name_title_and_delta() -> None:
    card = {
        "id": "c1",
        "title": "Gain 5 Points",
        "description": "Gain 5.",
        "canonical": {
            "timing": "immediate",
            "target": "self",
            "placement": "self",
            "ops": [{"op": "add_points", "args": {"amount": 5, "target": "self"}}],
        },
    }
    room = _playing_room(card)
    with patch("tbwc.agent.graph.interpret_card", side_effect=AssertionError("no LLM")):
        asyncio.run(room.handle_action("p1", PlayMsg(card_id="c1")))
    played = [line for line in room.state.log if "played" in line]
    assert played, room.state.log
    assert "Alice played Gain 5 Points" in played[-1]
    assert "Alice +5" in played[-1]


def test_play_log_shows_other_players_deltas() -> None:
    card = {
        "id": "c2",
        "title": "Everyone Else Loses 2",
        "description": "All others lose 2.",
        "canonical": {
            "timing": "immediate",
            "target": "all",
            "placement": "self",
            "ops": [{"op": "subtract_points", "args": {"amount": 2, "target": "all_others"}}],
        },
    }
    room = _playing_room(card)
    with patch("tbwc.agent.graph.interpret_card", side_effect=AssertionError("no LLM")):
        asyncio.run(room.handle_action("p1", PlayMsg(card_id="c2")))
    line = [line for line in room.state.log if "played" in line][-1]
    assert "Alice played Everyone Else Loses 2" in line
    assert "Bob -2" in line


def test_play_log_no_delta_for_effectless_card() -> None:
    card = {"id": "c3", "title": "Just Flavor", "description": "Nothing mechanical."}
    room = _playing_room(card)
    # A card with no ops resolves to a CustomNoteOp (no score change).
    with patch(
        "tbwc.agent.graph.interpret_card",
        return_value={"program": EffectProgram(ops=[]), "snippet": None, "verdict": "invalid"},
    ):
        asyncio.run(room.handle_action("p1", PlayMsg(card_id="c3")))
    line = [line for line in room.state.log if "played" in line][-1]
    assert "Alice played Just Flavor" in line
    assert "+" not in line and "-" not in line
