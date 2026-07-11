"""Tests for the epilogue flow (EpilogueManager + Room wiring)."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

from models.ws_messages import EpilogueVoteMsg
from board.rooms.epilogue import EpilogueManager
from board.rooms.room import Room


def test_all_votes_in_requires_every_player_on_every_card() -> None:
    mgr = EpilogueManager(player_ids=["p1", "p2"])

    async def run() -> None:
        conns = AsyncMock()
        await mgr.start([{"id": "c1", "title": "A", "description": "a"}], conns)

    asyncio.run(run())
    assert mgr.record_vote("p1", "c1", keep=True) is False  # p2 hasn't voted
    assert mgr.record_vote("p2", "c1", keep=True) is True  # now all in


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
    room.state = room.state.model_copy(update={"cards": {"c1": {"id": "c1", "title": "T", "description": "D"}}})
    room.connections.connect("p1", AsyncMock())
    with patch("agent.rag.store.upsert_card"):
        asyncio.run(room.start_epilogue())
        assert room.state.phase == "epilogue"
        asyncio.run(room.handle_action("p1", EpilogueVoteMsg(card_id="c1", keep=True)))
    assert room.state.phase == "ended"
