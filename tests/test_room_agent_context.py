"""Bead f1l (C10) — Room wires the NEW single agent (agent.runtime.run_agent).

These focused tests assert the board→agent contract that C10 establishes:

- A free-text card played through the room calls ``run_agent`` with the live
  ``GameState`` plus the ``actor_id`` (the player who played it) and ``creator_id``
  (the card's author) — the context the persona needs to read the board.
- An ``InterpretResult`` with ``verdict="ok"`` and a program with ops is applied;
  an ``invalid`` verdict falls back to a CustomNoteOp (never a silent no-op).
- The agent's ``comment`` rides along on the ``card_interpreted`` broadcast so
  D1/D2 can surface/persist it later.
"""

from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, patch

from agent.contract import InterpretResult
from models.effects import AddPointsOp, EffectProgram
from models.game_state import GameState
from models.ws_messages import PlayMsg
from board.rooms.room import Room


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
    return r


def test_run_agent_receives_state_actor_and_creator() -> None:
    # A free-text card (no compilable ops) routes to the agent, which must get the
    # live GameState + actor_id (the player) + creator_id (the card's author).
    card = {"id": "c1", "title": "Mystery", "description": "Something happens.", "creator_id": "author-9"}
    room = _room_with_card(card)
    room.connections.connect("p1", AsyncMock())
    room.connections.connect("p2", AsyncMock())

    ok = InterpretResult(program=EffectProgram(ops=[AddPointsOp(target="self", amount=1)]), verdict="ok")
    with patch("agent.runtime.run_agent", return_value=ok) as spy:
        asyncio.run(room.handle_action("p1", PlayMsg(card_id="c1")))

    spy.assert_called_once()
    call = spy.call_args
    assert call.args[0] == "Mystery"  # title
    assert call.args[1] == "Something happens."  # description
    assert isinstance(call.args[2], GameState)  # live GameState value, not a board handle
    assert call.args[3] == "p1"  # actor_id == the playing player
    assert call.kwargs["creator_id"] == "author-9"  # creator_id from the card


def test_ok_verdict_program_is_applied() -> None:
    card = {"id": "c2", "title": "Boon", "description": "Gain points.", "creator_id": "p1"}
    room = _room_with_card(card)
    room.connections.connect("p1", AsyncMock())
    room.connections.connect("p2", AsyncMock())

    ok = InterpretResult(program=EffectProgram(ops=[AddPointsOp(target="self", amount=4)]), verdict="ok")
    with patch("agent.runtime.run_agent", return_value=ok):
        asyncio.run(room.handle_action("p1", PlayMsg(card_id="c2")))

    assert room.state.get_player("p1").score == 4
    assert "c2" in room.state.discard


def test_invalid_verdict_falls_back_to_custom_note_no_silent_noop() -> None:
    card = {"id": "c3", "title": "Gibberish", "description": "???", "creator_id": "p1"}
    room = _room_with_card(card)
    room.connections.connect("p1", AsyncMock())
    room.connections.connect("p2", AsyncMock())

    invalid = InterpretResult(program=None, verdict="invalid", comment="No clue.")
    with patch("agent.runtime.run_agent", return_value=invalid):
        asyncio.run(room.handle_action("p1", PlayMsg(card_id="c3")))

    # No score change, but the play resolved with a log line (never silent).
    assert room.state.get_player("p1").score == 0
    assert "c3" in room.state.discard
    assert any("no mechanical effect" in line for line in room.state.log)


def test_comment_rides_on_card_interpreted_broadcast() -> None:
    # The agent's in-character comment must be carried on the card_interpreted
    # broadcast so D1/D2 can surface/persist it (C10 does NOT persist to state.log).
    card = {"id": "c4", "title": "Quip", "description": "free text", "creator_id": "p1"}
    room = _room_with_card(card)
    ws1 = AsyncMock()
    room.connections.connect("p1", ws1)
    room.connections.connect("p2", AsyncMock())

    result = InterpretResult(
        program=EffectProgram(ops=[AddPointsOp(target="self", amount=1)]),
        verdict="ok",
        comment="A bold move for such a small card.",
    )
    with patch("agent.runtime.run_agent", return_value=result):
        asyncio.run(room.handle_action("p1", PlayMsg(card_id="c4")))

    sent = [json.loads(c.args[0]) for c in ws1.send_text.call_args_list]
    interpreted = next(m for m in sent if m["type"] == "card_interpreted")
    assert interpreted["comment"] == "A bold move for such a small card."
    # Not yet persisted to the game log (that's D1).
    assert not any("A bold move" in line for line in room.state.log)
