"""Tests for the epilogue flow (EpilogueManager + Room wiring)."""

from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, patch

from models.ws_messages import EpilogueDoneMsg, EpilogueFinalizeMsg, EpilogueVoteMsg
from board.rooms.epilogue import EpilogueManager
from board.rooms.room import Room


def _start(mgr: EpilogueManager, cards: list[dict]) -> None:
    asyncio.run(mgr.start(cards, AsyncMock()))


def test_mark_done_finalizes_once_every_player_is_done() -> None:
    # A player who votes on the one card still needs to signal done — voting
    # alone no longer implies completion (that's the per-player DONE gate).
    mgr = EpilogueManager(player_ids=["p1", "p2"])
    _start(mgr, [{"id": "c1", "title": "A", "description": "a"}])
    mgr.record_vote("p1", "c1", keep=True)
    assert mgr.mark_done("p1") is False  # p2 hasn't signalled done
    assert mgr.mark_done("p2") is True  # now everyone's done


def test_mark_done_allows_skipping_all_votes() -> None:
    # Voting is skippable: a player can go straight to done without casting a
    # single vote, so a walk-away can't stall the room forever.
    mgr = EpilogueManager(player_ids=["p1", "p2"])
    _start(mgr, [{"id": "c1", "title": "A", "description": "a"}, {"id": "c2", "title": "B", "description": "b"}])
    assert mgr.mark_done("p1") is False
    assert mgr.mark_done("p2") is True
    assert mgr.all_done() is True


def test_tally_and_persist_upserts_kept_cards() -> None:
    mgr = EpilogueManager(player_ids=["p1", "p2"])
    cards = [
        {"id": "c1", "title": "Keep me", "description": "d1", "program": "{}"},
        {"id": "c2", "title": "Destroy me", "description": "d2"},
    ]

    async def run():
        await mgr.start(cards, AsyncMock())
        mgr.record_vote("p1", "c1", keep=True)
        mgr.record_vote("p2", "c1", keep=True)
        mgr.record_vote("p1", "c2", keep=False)
        mgr.record_vote("p2", "c2", keep=False)
        with patch("agent.rag.store.upsert_card") as mock_upsert:
            result = await mgr.tally_and_persist()
        return result, mock_upsert

    result, mock_upsert = asyncio.run(run())
    assert "c1" in result.kept
    assert "c2" in result.destroyed
    mock_upsert.assert_called_once()
    _, kwargs = mock_upsert.call_args
    assert kwargs["card_id"] == "c1"
    assert kwargs["source"] == "player"


def test_room_epilogue_vote_without_start_errors() -> None:
    room = Room("ABCDEF")
    room.add_player("p1", "Alice")
    ws1 = AsyncMock()
    room.connections.connect("p1", ws1)
    asyncio.run(room.handle_action("p1", EpilogueVoteMsg(card_id="c1", keep=True)))
    import json

    sent = [json.loads(c.args[0]) for c in ws1.send_text.call_args_list]
    assert any(m["type"] == "error" for m in sent)


def test_room_start_epilogue_and_complete_vote() -> None:
    room = Room("ABCDEF")
    room.add_player("p1", "Alice")
    room.state = room.state.model_copy(
        update={"cards": {"c1": {"id": "c1", "title": "T", "description": "D", "origin": "authored"}}}
    )
    room.connections.connect("p1", AsyncMock())
    with patch("agent.rag.store.upsert_card"):
        asyncio.run(room.start_epilogue())
        assert room.state.phase == "epilogue"
        asyncio.run(room.handle_action("p1", EpilogueVoteMsg(card_id="c1", keep=True)))
        assert room.state.phase == "epilogue"  # voting alone doesn't finalize
        asyncio.run(room.handle_action("p1", EpilogueDoneMsg()))
    assert room.state.phase == "ended"


def test_start_epilogue_filters_vote_pool_to_authored_cards() -> None:
    # Blanks and shipped seed cards must never reach the vote pool; cards
    # authored this game or kept from a previous game (a RAG re-entry, source
    # "player") do.
    room = Room("ABCDEF")
    room.add_player("p1", "Alice")
    room.state = room.state.model_copy(
        update={
            "cards": {
                "blank-0": {"id": "blank-0", "title": "", "description": "", "blank": True, "origin": "blank"},
                "seed-0": {"id": "seed-0", "title": "Seed Card", "description": "shipped", "origin": "seed"},
                "authored-this-game": {
                    "id": "authored-this-game",
                    "title": "Fresh",
                    "description": "written this game",
                    "creator_id": "p1",
                    "origin": "authored",
                },
                "kept-from-prior-game": {
                    "id": "kept-from-prior-game",
                    "title": "Legacy",
                    "description": "RAG re-entry",
                    "creator_id": "player",
                    "origin": "authored",
                },
            }
        }
    )
    ws1 = AsyncMock()
    room.connections.connect("p1", ws1)
    asyncio.run(room.start_epilogue())

    epilogue_msgs = [json.loads(c.args[0]) for c in ws1.send_text.call_args_list if c.args]
    epilogue_msg = next(m for m in epilogue_msgs if m["type"] == "epilogue")
    voted_ids = {c["id"] for c in epilogue_msg["cards"]}
    assert voted_ids == {"authored-this-game", "kept-from-prior-game"}


def test_epilogue_host_can_finalize_early() -> None:
    # p1 is the host (first joiner). p2 never votes or signals done; the host
    # finalizes anyway, so a stalled/walked-away player can't block the room.
    room = Room("ABCDEF")
    room.add_player("p1", "Alice")
    room.add_player("p2", "Bob")
    room.state = room.state.model_copy(
        update={"cards": {"c1": {"id": "c1", "title": "T", "description": "D", "origin": "authored"}}}
    )
    room.connections.connect("p1", AsyncMock())
    room.connections.connect("p2", AsyncMock())
    with patch("agent.rag.store.upsert_card"):
        asyncio.run(room.start_epilogue())
        asyncio.run(room.handle_action("p1", EpilogueVoteMsg(card_id="c1", keep=True)))
        asyncio.run(room.handle_action("p1", EpilogueFinalizeMsg()))
    assert room.state.phase == "ended"


def test_epilogue_finalize_rejects_non_host() -> None:
    room = Room("ABCDEF")
    room.add_player("p1", "Alice")
    room.add_player("p2", "Bob")
    room.state = room.state.model_copy(
        update={"cards": {"c1": {"id": "c1", "title": "T", "description": "D", "origin": "authored"}}}
    )
    room.connections.connect("p1", AsyncMock())
    ws2 = AsyncMock()
    room.connections.connect("p2", ws2)
    asyncio.run(room.start_epilogue())
    asyncio.run(room.handle_action("p2", EpilogueFinalizeMsg()))
    assert room.state.phase == "epilogue"
    sent = [json.loads(c.args[0]) for c in ws2.send_text.call_args_list]
    assert any(m["type"] == "error" for m in sent)


def test_epilogue_unvoted_card_abstains_and_defaults_to_kept() -> None:
    # p2 walks away without voting at all; going straight to done abstains them
    # on every card, and the tie-defaults-to-keep rule keeps it.
    room = Room("ABCDEF")
    room.add_player("p1", "Alice")
    room.add_player("p2", "Bob")
    room.state = room.state.model_copy(
        update={"cards": {"c1": {"id": "c1", "title": "T", "description": "D", "origin": "authored"}}}
    )
    room.connections.connect("p1", AsyncMock())
    room.connections.connect("p2", AsyncMock())
    with patch("agent.rag.store.upsert_card"):
        asyncio.run(room.start_epilogue())
        asyncio.run(room.handle_action("p1", EpilogueDoneMsg()))
        asyncio.run(room.handle_action("p2", EpilogueDoneMsg()))
    assert room.state.phase == "ended"
    assert any("Kept: 1" in line for line in room.state.log)
