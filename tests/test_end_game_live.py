"""Bead 13v — EndGameOp + live win-condition evaluation during play.

Before this bead, the ONLY end-game path was deck exhaustion: `evaluate_win_condition`
was never checked during play, so an "End the Game" card had no live effect and
`set_win_condition(first_to, N)` never actually ended anything until the deck ran dry.
These tests drive a room through the deterministic compiled-ops path (`canonical.ops`)
so no LLM is involved, and assert the game ends immediately — deck untouched — rather
than waiting for `_advance_turn`'s deck-exhaustion check.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

from models.ws_messages import PlayMsg
from board.rooms.room import Room


def _mid_deck_room(p1_hand: list[str], cards: dict, deck: list[str]) -> Room:
    """A two-player room mid-game: deck non-empty, both players connected, p1 active."""
    r = Room("ABCDEF")
    r.add_player("p1", "Alice")
    r.add_player("p2", "Bob")
    players = [
        r.state.players[0].model_copy(update={"hand": p1_hand}),
        r.state.players[1],
    ]
    r.state = r.state.model_copy(update={"phase": "playing", "deck": deck, "cards": cards, "players": players})
    r._has_drawn = True  # skip the draw-first gate; unrelated to what's under test
    r.connections.connect("p1", AsyncMock())
    r.connections.connect("p2", AsyncMock())
    return r


def test_end_game_card_ends_immediately_without_deck_exhaustion() -> None:
    card = {
        "id": "endit",
        "title": "End The Game",
        "description": "The game ends right now.",
        "canonical": {"ops": [{"op": "end_game", "args": {}}]},
    }
    room = _mid_deck_room(["endit"], {"endit": card}, deck=["d1", "d2", "d3"])

    asyncio.run(room.handle_action("p1", PlayMsg(card_id="endit")))

    assert room.state.phase in ("results", "ended")
    assert room.state.deck == ["d1", "d2", "d3"]  # untouched — did not wait for exhaustion
    assert room._deck_exhausted is False


def test_end_game_authoring_synonym_win_the_game_compiles() -> None:
    card = {
        "id": "win1",
        "title": "You Win",
        "description": "You win the game.",
        "canonical": {"ops": [{"op": "win_the_game", "args": {}}]},
    }
    room = _mid_deck_room(["win1"], {"win1": card}, deck=["d1"])

    asyncio.run(room.handle_action("p1", PlayMsg(card_id="win1")))

    assert room.state.phase in ("results", "ended")
    assert room.state.deck == ["d1"]


def test_set_win_condition_first_to_ends_the_moment_threshold_is_reached() -> None:
    # One card sets a first_to(10) win condition and, in the SAME play, scores
    # enough points to hit it — the game must end right away, mid-deck.
    card = {
        "id": "rush",
        "title": "Rush To Ten",
        "description": "First to 10 points wins; gain 10 points.",
        "canonical": {
            "ops": [
                {"op": "set_win_condition", "args": {"kind": "first_to", "threshold": 10}},
                {"op": "add_points", "args": {"target": "self", "amount": 10}},
            ]
        },
    }
    room = _mid_deck_room(["rush"], {"rush": card}, deck=["d1", "d2"])

    asyncio.run(room.handle_action("p1", PlayMsg(card_id="rush")))

    assert room.state.get_player("p1").score == 10
    assert room.state.win_condition.kind == "first_to"
    assert room.state.phase in ("results", "ended")
    assert room.state.deck == ["d1", "d2"]
    assert room._deck_exhausted is False


def test_set_win_condition_first_to_not_yet_reached_keeps_playing() -> None:
    card = {
        "id": "setup_only",
        "title": "First To Ten",
        "description": "First to 10 points wins.",
        "canonical": {"ops": [{"op": "set_win_condition", "args": {"kind": "first_to", "threshold": 10}}]},
    }
    room = _mid_deck_room(["setup_only"], {"setup_only": card}, deck=["d1", "d2"])

    asyncio.run(room.handle_action("p1", PlayMsg(card_id="setup_only")))

    assert room.state.win_condition.kind == "first_to"
    assert room.state.phase == "playing"
    assert room.state.turn_index == 1  # turn advanced normally, no early end
