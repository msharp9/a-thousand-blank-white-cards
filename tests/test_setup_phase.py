"""Tests for the two-step game-start SETUP phase (beads 70n.8 + 70n.9).

The start flow is: lobby -> (StartMsg) -> setup (build a 30-card pre-made pool,
each non-spectator authors CARDS_TO_AUTHOR cards) -> (StartMsg) -> playing
(finalise the deck, deal starting hands). During setup, authoring a card does
NOT call the LLM.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

from conftest import drive_to_playing

from models.ws_messages import CreateCardMsg, StartMsg
from board.rooms.room import CARDS_TO_AUTHOR, PREMADE_POOL_SIZE, STARTING_HAND_SIZE, Room


def _room_two_players() -> Room:
    room = Room("ABCDEF")
    room.add_player("p1", "Alice")
    room.add_player("p2", "Bob")
    room.connections.connect("p1", AsyncMock())
    room.connections.connect("p2", AsyncMock())
    return room


def test_lobby_start_enters_setup_and_seeds_premade_pool() -> None:
    room = _room_two_players()
    asyncio.run(room.handle_action("p1", StartMsg()))

    assert room.state.phase == "setup"
    # The pre-made pool is seeded into the registry and the deck (as ids).
    assert len(room.state.cards) == PREMADE_POOL_SIZE
    assert len(room.state.deck) == PREMADE_POOL_SIZE
    # No blanks in the pre-made pool.
    assert not any("blank" in cid for cid in room.state.deck)
    # Nothing dealt yet.
    for p in room.state.players:
        assert p.hand == []


def test_setup_snapshot_reports_progress_and_cards_to_author() -> None:
    room = _room_two_players()
    asyncio.run(room.handle_action("p1", StartMsg()))

    snap = room.snapshot()
    assert snap["cards_to_author"] == CARDS_TO_AUTHOR
    assert snap["setup_progress"] == {"p1": 0, "p2": 0}


def test_authoring_during_setup_increments_progress_and_skips_llm() -> None:
    room = _room_two_players()
    asyncio.run(room.handle_action("p1", StartMsg()))

    with patch("agent.runtime.run_agent") as mock_interp:
        asyncio.run(room.handle_action("p1", CreateCardMsg(title="Mine", description="gain 1 point")))
        asyncio.run(room.handle_action("p1", CreateCardMsg(title="Mine2", description="gain 1 point")))

    # The agent is never called during setup authoring.
    mock_interp.assert_not_called()
    # p1's authored count advanced; p2 untouched.
    assert room.snapshot()["setup_progress"] == {"p1": 2, "p2": 0}
    # The authored cards carry the creator id and were registered.
    authored = [c for c in room.state.cards.values() if c.get("creator_id") == "p1"]
    assert len(authored) == 2


def test_authoring_up_to_the_limit_during_setup_is_allowed() -> None:
    room = _room_two_players()
    asyncio.run(room.handle_action("p1", StartMsg()))

    for i in range(CARDS_TO_AUTHOR):
        asyncio.run(room.handle_action("p1", CreateCardMsg(title=f"c{i}", description="gain 1 point")))

    assert room.snapshot()["setup_progress"]["p1"] == CARDS_TO_AUTHOR
    authored = [c for c in room.state.cards.values() if c.get("creator_id") == "p1"]
    assert len(authored) == CARDS_TO_AUTHOR


def test_authoring_over_the_limit_during_setup_is_rejected() -> None:
    import json

    room = _room_two_players()
    ws1 = AsyncMock()
    room.connections.connect("p1", ws1)
    asyncio.run(room.handle_action("p1", StartMsg()))  # -> setup

    # Author up to the cap.
    for i in range(CARDS_TO_AUTHOR):
        asyncio.run(room.handle_action("p1", CreateCardMsg(title=f"c{i}", description="gain 1 point")))

    ws1.reset_mock()
    # The (CARDS_TO_AUTHOR + 1)th create must be rejected and add no card.
    asyncio.run(room.handle_action("p1", CreateCardMsg(title="extra", description="gain 1 point")))

    sent_types = [json.loads(c.args[0])["type"] for c in ws1.send_text.call_args_list]
    assert "error" in sent_types
    # Still exactly CARDS_TO_AUTHOR authored — the over-limit card was not registered.
    authored = [c for c in room.state.cards.values() if c.get("creator_id") == "p1"]
    assert len(authored) == CARDS_TO_AUTHOR
    assert not any(c.get("title") == "extra" for c in room.state.cards.values())


def test_create_card_rejected_during_playing_phase() -> None:
    import json

    room = _room_two_players()
    ws1 = AsyncMock()
    room.connections.connect("p1", ws1)
    # Authoring is setup-only: a mid-game create_card gets the standard error
    # envelope, never reaches the agent, and registers no card.
    room.state = room.state.model_copy(update={"phase": "playing"})

    with patch("agent.runtime.run_agent") as mock_agent:
        asyncio.run(room.handle_action("p1", CreateCardMsg(title="mid", description="do something")))

    mock_agent.assert_not_called()
    sent = [json.loads(c.args[0]) for c in ws1.send_text.call_args_list]
    assert any(m["type"] == "error" for m in sent)
    assert room.state.cards == {}


def test_start_during_setup_with_players_behind_errors_and_stays_in_setup() -> None:
    room = _room_two_players()
    ws1 = AsyncMock()
    room.connections.connect("p1", ws1)
    asyncio.run(room.handle_action("p1", StartMsg()))  # -> setup

    # Only p1 authors the required cards; p2 is behind.
    for i in range(CARDS_TO_AUTHOR):
        asyncio.run(room.handle_action("p1", CreateCardMsg(title=f"c{i}", description="gain 1 point")))

    ws1.reset_mock()
    asyncio.run(room.handle_action("p1", StartMsg()))  # gate should block

    import json

    sent_types = [json.loads(c.args[0])["type"] for c in ws1.send_text.call_args_list]
    assert "error" in sent_types
    assert room.state.phase == "setup"


def test_full_flow_two_players_reaches_playing_with_dealt_hands() -> None:
    room = _room_two_players()
    drive_to_playing(room, ["p1", "p2"])

    assert room.state.phase == "playing"
    # The shuffled turn_order's first player's turn began with the automatic
    # draw on top of the deal — turn order is randomized, not host-first.
    first_id = room.state.active_player().id
    other_id = "p2" if first_id == "p1" else "p1"
    assert len(room.state.get_player(first_id).hand) == STARTING_HAND_SIZE + room.state.draw_count
    assert len(room.state.get_player(other_id).hand) == STARTING_HAND_SIZE
    assert room.state.deck  # deck is non-empty after dealing


def test_auto_start_when_last_player_finishes_authoring() -> None:
    # No {type:"start"} is ever sent for the setup->playing transition: it fires
    # automatically once the final player authors their last required card.
    room = _room_two_players()
    asyncio.run(room.handle_action("p1", StartMsg()))  # lobby -> setup

    # p1 authors all their cards first — game must NOT start yet (p2 behind).
    for i in range(CARDS_TO_AUTHOR):
        asyncio.run(room.handle_action("p1", CreateCardMsg(title=f"a{i}", description="gain 1 point")))
    assert room.state.phase == "setup"

    # p2 authors all but the last — still setup.
    for i in range(CARDS_TO_AUTHOR - 1):
        asyncio.run(room.handle_action("p2", CreateCardMsg(title=f"b{i}", description="gain 1 point")))
    assert room.state.phase == "setup"

    # p2 authors the FINAL required card — the game auto-transitions to playing
    # WITHOUT any StartMsg, with hands dealt and the first turn begun.
    asyncio.run(room.handle_action("p2", CreateCardMsg(title="b-last", description="gain 1 point")))
    assert room.state.phase == "playing"
    # First turn begun: the shuffled turn_order's first player was auto-drawn
    # to — turn order is randomized, not host-first.
    first_id = room.state.active_player().id
    other_id = "p2" if first_id == "p1" else "p1"
    assert len(room.state.get_player(first_id).hand) == STARTING_HAND_SIZE + room.state.draw_count
    assert len(room.state.get_player(other_id).hand) == STARTING_HAND_SIZE
    assert room._has_drawn is True


def test_authoring_while_players_behind_does_not_auto_start() -> None:
    room = _room_two_players()
    asyncio.run(room.handle_action("p1", StartMsg()))  # lobby -> setup

    # Only p1 authors the full quota; p2 has authored nothing.
    for i in range(CARDS_TO_AUTHOR):
        asyncio.run(room.handle_action("p1", CreateCardMsg(title=f"c{i}", description="gain 1 point")))

    # p2 is still behind, so the game must remain in setup.
    assert room.state.phase == "setup"
    for p in room.state.players:
        assert p.hand == []


def test_single_player_game_auto_starts() -> None:
    room = Room("SOLO01")
    room.add_player("p1", "Solo")
    room.connections.connect("p1", AsyncMock())
    asyncio.run(room.handle_action("p1", StartMsg()))  # lobby -> setup
    assert room.state.phase == "setup"

    for i in range(CARDS_TO_AUTHOR - 1):
        asyncio.run(room.handle_action("p1", CreateCardMsg(title=f"s{i}", description="gain 1 point")))
    assert room.state.phase == "setup"

    # The final required card triggers the auto-start for the lone player.
    asyncio.run(room.handle_action("p1", CreateCardMsg(title="s-last", description="gain 1 point")))
    assert room.state.phase == "playing"
    assert len(room.state.get_player("p1").hand) == STARTING_HAND_SIZE + room.state.draw_count


def test_finalized_deck_size_for_two_players() -> None:
    # 30 premade + 10 authored (5 each) + 10 blanks (5/player) = 50 total, minus
    # 10 dealt (5 each) and the first player's auto-draw = 39 in the deck.
    room = _room_two_players()
    drive_to_playing(room, ["p1", "p2"])

    total_hands = sum(len(p.hand) for p in room.state.players)
    assert total_hands == 2 * STARTING_HAND_SIZE + room.state.draw_count
    assert len(room.state.deck) + total_hands == 50
    assert len(room.state.deck) == 39
