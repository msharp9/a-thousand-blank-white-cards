"""Bead phy.2 — game actions are frozen while a play is being interpreted (brewing).

``handle_action`` holds the room lock across a play's whole resolution —
including the threaded ``run_agent`` call — so a second game action sent
mid-brew used to queue on the lock and execute against the post-resolution
state (succeeding whenever the first play ended on a non-consuming path such
as a prompt_choice). The room now rejects play/pass/end_turn/create_card/start
up front while ``_resolving_play`` is set, and the flag always clears
(try/finally) even when interpretation crashes.
"""

from __future__ import annotations

import asyncio
import json
import threading
from unittest.mock import AsyncMock, patch

import pytest

from agent.contract import InterpretResult
from models.effects import AddPointsOp, EffectProgram
from models.ws_messages import CreateCardMsg, PlayMsg
from board.rooms.room import Room

FREEZE_MESSAGE = "Waiting for the current play to finish resolving"


def _playing_room() -> Room:
    """Two-player playing room: p1 (active) holds a free-text card and a
    compiled card; p2 holds a filler card."""
    r = Room("ABCDEF")
    r.add_player("p1", "Alice")
    r.add_player("p2", "Bob")
    cards = {
        "c1": {"id": "c1", "title": "Mystery", "description": "Something happens.", "creator_id": "p1"},
        "c2": {
            "id": "c2",
            "title": "Gain 5",
            "description": "Gain 5 points.",
            "canonical": {
                "timing": "immediate",
                "target": "self",
                "placement": "self",
                "ops": [{"op": "add_points", "args": {"amount": 5, "target": "self"}}],
            },
        },
        "other": {"id": "other", "title": "Filler", "description": "Nothing."},
    }
    new_players = [
        p.model_copy(update={"hand": ["c1", "c2"]}) if p.id == "p1" else p.model_copy(update={"hand": ["other"]})
        for p in r.state.players
    ]
    r.state = r.state.model_copy(update={"phase": "playing", "cards": cards, "players": new_players})
    r._has_drawn = True
    r.connections.connect("p1", AsyncMock())
    r.connections.connect("p2", AsyncMock())
    return r


def _sent_messages(room: Room, player_id: str) -> list[dict]:
    ws = room.connections.get(player_id)
    return [json.loads(call.args[0]) for call in ws.send_text.await_args_list]


def _errors(room: Room, player_id: str) -> list[str]:
    return [m["message"] for m in _sent_messages(room, player_id) if m.get("type") == "error"]


def test_second_play_rejected_while_first_is_brewing() -> None:
    # Fire a second play (and a create_card from another player) while the
    # first play's agent interpretation is parked in its thread; both must be
    # rejected immediately, and the room must end up clean once the slow
    # interpretation finishes.
    room = _playing_room()
    started = threading.Event()
    release = threading.Event()

    def slow_agent(*args, **kwargs):
        started.set()
        assert release.wait(timeout=5), "test never released the fake agent"
        return InterpretResult(
            program=EffectProgram(ops=[AddPointsOp(target="self", amount=3)]),
            snippet=None,
            verdict="ok",
        )

    async def scenario() -> None:
        with patch("agent.runtime.run_agent", side_effect=slow_agent):
            first = asyncio.create_task(room.handle_action("p1", PlayMsg(card_id="c1")))
            await asyncio.to_thread(started.wait, 5)
            assert room._resolving_play == "c1"

            await room.handle_action("p1", PlayMsg(card_id="c2"))
            await room.handle_action("p2", CreateCardMsg(title="Sneaky", description="gain 1 point"))

            # Both rejected with the freeze envelope; nothing was applied.
            assert FREEZE_MESSAGE in _errors(room, "p1")
            assert FREEZE_MESSAGE in _errors(room, "p2")
            assert "c2" in room.state.get_player("p1").hand
            assert room.state.get_player("p1").score == 0
            assert all(c.get("title") != "Sneaky" for c in room.state.cards.values())

            release.set()
            await first

        # The first play resolved normally and the freeze cleared.
        assert room._resolving_play is None
        assert room.state.get_player("p1").score == 3
        assert "c1" in room.state.discard
        assert "c2" in room.state.get_player("p1").hand
        assert room.state.turn_index == 1  # turn advanced to p2

    asyncio.run(scenario())


def test_freeze_clears_when_agent_raises() -> None:
    # run_agent raising is swallowed by _resolve_plan's deterministic fallback;
    # the play still completes and the freeze never lingers.
    room = _playing_room()
    with patch("agent.runtime.run_agent", side_effect=RuntimeError("boom")):
        asyncio.run(room.handle_action("p1", PlayMsg(card_id="c1")))
    assert room._resolving_play is None
    assert "c1" in room.state.discard
    assert room.state.turn_index == 1


def test_freeze_clears_when_resolution_raises() -> None:
    # A crash that escapes _handle_play entirely (nothing catches it) must
    # still clear the freeze, leaving the room actionable afterwards.
    room = _playing_room()
    with patch.object(Room, "_resolve_plan", side_effect=RuntimeError("kaboom")):
        with pytest.raises(RuntimeError):
            asyncio.run(room.handle_action("p1", PlayMsg(card_id="c1")))
    assert room._resolving_play is None

    # The room is not wedged: the (still-active) player can play the compiled card.
    with patch("agent.runtime.run_agent", side_effect=AssertionError("agent must not be called")):
        asyncio.run(room.handle_action("p1", PlayMsg(card_id="c2")))
    assert room.state.get_player("p1").score == 5
    assert "c2" in room.state.discard
