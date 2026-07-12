"""Bead 70n.13 — end-of-game resolves kept-card scoring, then shows results.

When the deck is exhausted and the drawer's turn ends, `_end_game` now:
  1. applies end-of-game (kept-in-hand) card effects via resolve_end_of_game,
  2. computes winner_ids from the final scores,
  3. lands on the results screen (phase="results"),
the host then advances into the epilogue vote (phase="epilogue") via
`epilogue_start`, and the epilogue completing transitions to phase="ended".
"""

from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock

import pytest

from engine.scoring import evaluate_win_condition, resolve_end_of_game
from models.ws_messages import EpilogueDoneMsg, EpilogueStartMsg, EpilogueVoteMsg, PassMsg
from board.rooms.room import Room


def _ended_room(p1_hand: list[str], cards: dict, scores: tuple[int, int]) -> Room:
    r = Room("ABCDEF")
    r.add_player("p1", "Alice")
    r.add_player("p2", "Bob")
    players = [
        r.state.players[0].model_copy(update={"score": scores[0], "hand": p1_hand}),
        r.state.players[1].model_copy(update={"score": scores[1]}),
    ]
    r.state = r.state.model_copy(update={"phase": "playing", "deck": [], "cards": cards, "players": players})
    r._deck_exhausted = True
    r._has_drawn = True
    r.connections.connect("p1", AsyncMock())
    r.connections.connect("p2", AsyncMock())
    return r


def test_end_game_shows_results_with_winner() -> None:
    room = _ended_room([], {}, scores=(3, 7))
    asyncio.run(room.handle_action("p1", PassMsg()))
    assert room.state.phase == "results"
    assert room.state.winner_ids == ["p2"]


def test_end_game_applies_kept_card_bonus_before_deciding_winner() -> None:
    # p1 trails 3-7 but holds a "worth 10 if kept" card. The game ends on p2's
    # turn (p2 has nothing playable and passes an exhausted deck), so p1's card
    # is still in hand at game end → +10 → p1 wins 13-7.
    kept = {
        "id": "keep10",
        "title": "Worth 10 If Kept",
        "description": "Worth 10 points if still in your hand at game end.",
        "canonical": {
            "timing": "modifier",
            "target": "self",
            "placement": "self",
            "trigger": "on_game_end",
            "ops": [{"op": "add_points", "args": {"amount": 10, "target": "self"}}],
        },
    }
    room = _ended_room([], {"keep10": kept}, scores=(3, 7))
    # p1 holds the kept card; p2 is active with an empty hand and ends the game.
    p1 = room.state.players[0].model_copy(update={"hand": ["keep10"]})
    room.state = room.state.model_copy(update={"players": [p1, room.state.players[1]], "turn_index": 1})
    asyncio.run(room.handle_action("p2", PassMsg()))
    assert room.state.get_player("p1").score == 13
    assert room.state.winner_ids == ["p1"]
    assert room.state.phase == "results"


def test_end_game_logs_kept_card_application_before_winner_line() -> None:
    # Each on_game_end card application must be visible in state.log (one line
    # per card, "Game end: <holder>'s '<title>' (<deltas>)") BEFORE the "Game
    # over! Winner(s): ..." line — otherwise the score jump is silent.
    kept = {
        "id": "keep10",
        "title": "Worth 10 If Kept",
        "description": "Worth 10 points if still in your hand at game end.",
        "canonical": {
            "timing": "modifier",
            "target": "self",
            "placement": "self",
            "trigger": "on_game_end",
            "ops": [{"op": "add_points", "args": {"amount": 10, "target": "self"}}],
        },
    }
    room = _ended_room([], {"keep10": kept}, scores=(3, 7))
    p1 = room.state.players[0].model_copy(update={"hand": ["keep10"]})
    room.state = room.state.model_copy(update={"players": [p1, room.state.players[1]], "turn_index": 1})

    asyncio.run(room.handle_action("p2", PassMsg()))

    game_end_lines = [line for line in room.state.log if line.startswith("Game end:")]
    assert len(game_end_lines) == 1
    assert "Alice's 'Worth 10 If Kept'" in game_end_lines[0]
    assert "Alice +10" in game_end_lines[0]

    game_end_index = room.state.log.index(game_end_lines[0])
    winner_index = next(i for i, line in enumerate(room.state.log) if line.startswith("Game over!"))
    assert game_end_index < winner_index


def test_dev_force_end_game_uses_real_scoring_path() -> None:
    # p1 trails 3-7 but holds a "worth 10 if kept" on_game_end card. Forcing the
    # end game must run the real resolve_end_of_game → evaluate_win_condition path,
    # so p1's +10 lands and p1 wins 13-7, landing on the results screen.
    kept = {
        "id": "keep10",
        "title": "Worth 10 If Kept",
        "description": "Worth 10 points if still in your hand at game end.",
        "canonical": {
            "timing": "modifier",
            "target": "self",
            "placement": "self",
            "trigger": "on_game_end",
            "ops": [{"op": "add_points", "args": {"amount": 10, "target": "self"}}],
        },
    }
    room = _ended_room(["keep10"], {"keep10": kept}, scores=(3, 7))
    # Independently compute what a real deck-exhaustion end game would decide.
    expected_state, _apps = resolve_end_of_game(room.state)
    expected_winners = evaluate_win_condition(expected_state)

    asyncio.run(room.dev_force_end_game())

    assert room.state.phase == "results"
    assert room.state.get_player("p1").score == 13
    assert room.state.winner_ids == expected_winners == ["p1"]


def test_dev_force_end_game_rejects_when_not_playing() -> None:
    room = Room("LOBBY1")
    room.add_player("p1", "Alice")
    with pytest.raises(ValueError, match="game is not in progress"):
        asyncio.run(room.dev_force_end_game())


def test_epilogue_vote_completion_reaches_ended() -> None:
    # After the host advances results -> epilogue, both players voting then
    # signalling done finalizes -> phase="ended".
    authored = {"a1": {"id": "a1", "title": "Custom", "description": "x", "creator_id": "p1", "origin": "authored"}}
    room = _ended_room([], authored, scores=(1, 2))
    asyncio.run(room.handle_action("p1", PassMsg()))
    assert room.state.phase == "results"
    asyncio.run(room.handle_action("p1", EpilogueStartMsg()))
    assert room.state.phase == "epilogue"
    # Both real players vote on the single authored card, then mark done.
    asyncio.run(room.handle_action("p1", EpilogueVoteMsg(card_id="a1", keep=True)))
    asyncio.run(room.handle_action("p2", EpilogueVoteMsg(card_id="a1", keep=True)))
    asyncio.run(room.handle_action("p1", EpilogueDoneMsg()))
    assert room.state.phase == "epilogue"  # p2 hasn't signalled done yet
    asyncio.run(room.handle_action("p2", EpilogueDoneMsg()))
    assert room.state.phase == "ended"
    assert room.state.epilogue_result is not None
    assert [c.id for c in room.state.epilogue_result.kept] == ["a1"]
    assert room.state.epilogue_result.kept[0].title == "Custom"
    assert room.state.epilogue_result.destroyed == []


def test_reconnect_snapshot_carries_epilogue_result() -> None:
    # A client that (re)joins after the vote finalizes only gets a fresh
    # snapshot() call — epilogue_result must ride it, not just live state.
    authored = {
        "keep-me": {
            "id": "keep-me",
            "title": "Keep Me",
            "description": "x",
            "creator_id": "p1",
            "origin": "authored",
        },
        "cut-me": {
            "id": "cut-me",
            "title": "Cut Me",
            "description": "y",
            "creator_id": "p1",
            "origin": "authored",
        },
    }
    room = _ended_room([], authored, scores=(1, 2))
    asyncio.run(room.handle_action("p1", PassMsg()))
    snap_before = room.snapshot()
    assert snap_before["phase"] == "results"
    assert snap_before["epilogue_result"] is None

    asyncio.run(room.handle_action("p1", EpilogueStartMsg()))
    asyncio.run(room.handle_action("p1", EpilogueVoteMsg(card_id="keep-me", keep=True)))
    asyncio.run(room.handle_action("p2", EpilogueVoteMsg(card_id="keep-me", keep=True)))
    asyncio.run(room.handle_action("p1", EpilogueVoteMsg(card_id="cut-me", keep=False)))
    asyncio.run(room.handle_action("p2", EpilogueVoteMsg(card_id="cut-me", keep=False)))
    asyncio.run(room.handle_action("p1", EpilogueDoneMsg()))
    asyncio.run(room.handle_action("p2", EpilogueDoneMsg()))
    assert room.state.phase == "ended"

    # Simulate a reconnecting client: a brand-new snapshot() call, no reliance
    # on anything from the live epilogue-vote session.
    snap_after = room.snapshot()
    assert snap_after["phase"] == "ended"
    assert snap_after["epilogue_result"]["kept"] == [{"id": "keep-me", "title": "Keep Me"}]
    assert snap_after["epilogue_result"]["destroyed"] == [{"id": "cut-me", "title": "Cut Me"}]


def test_epilogue_start_rejected_before_results_and_for_non_host() -> None:
    room = _ended_room([], {}, scores=(1, 2))
    ws2 = AsyncMock()
    room.connections.connect("p2", ws2)
    # Still "playing" — no results screen to advance from yet.
    asyncio.run(room.handle_action("p1", EpilogueStartMsg()))
    assert room.state.phase == "playing"

    asyncio.run(room.handle_action("p1", PassMsg()))
    assert room.state.phase == "results"

    # p2 is not the host.
    asyncio.run(room.handle_action("p2", EpilogueStartMsg()))
    assert room.state.phase == "results"
    sent = [c.args[0] for c in ws2.send_text.call_args_list]
    assert any(json.loads(m)["type"] == "error" for m in sent)

    asyncio.run(room.handle_action("p1", EpilogueStartMsg()))
    assert room.state.phase == "epilogue"
