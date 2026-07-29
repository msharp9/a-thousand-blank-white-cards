"""Tests for the epilogue flow (EpilogueManager + Room wiring)."""

from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, patch

from models.ws_messages import EpilogueDoneMsg, EpilogueFinalizeMsg, EpilogueVoteMsg
from board.rooms.deck import build_premade_pool
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


def test_kept_card_is_eligible_for_next_games_premade_pool() -> None:
    fake_vector = [0.1] * 1536
    with patch("agent.rag.store.embed_text_cached", return_value=fake_vector):
        from agent.rag.store import init_store, upsert_card

        init_store()
        upsert_card("seed-1", "Seed One", "First seed.", "{}", "seed")
        upsert_card("seed-2", "Seed Two", "Second seed.", "{}", "seed")

        mgr = EpilogueManager(player_ids=["p1"])

        async def keep_authored_card() -> None:
            await mgr.start(
                [{"id": "authored-1", "title": "A Keeper", "description": "Keep this card.", "canonical": {}}],
                AsyncMock(),
            )
            mgr.record_vote("p1", "authored-1", keep=True)
            await mgr.tally_and_persist()

        asyncio.run(keep_authored_card())
        cards, pool = build_premade_pool(count=3)

    assert set(pool) == {"seed-1", "seed-2", "authored-1"}
    assert cards["authored-1"]["origin"] == "authored"


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


def test_epilogue_unvoted_card_abstains_and_is_destroyed() -> None:
    # Both players walk away without voting; abstains leave the card at 0-0,
    # which is not a strict keep majority, so it is destroyed.
    room = Room("ABCDEF")
    room.add_player("p1", "Alice")
    room.add_player("p2", "Bob")
    room.state = room.state.model_copy(
        update={"cards": {"c1": {"id": "c1", "title": "T", "description": "D", "origin": "authored"}}}
    )
    room.connections.connect("p1", AsyncMock())
    room.connections.connect("p2", AsyncMock())
    with patch("agent.rag.store.delete_card"):
        asyncio.run(room.start_epilogue())
        asyncio.run(room.handle_action("p1", EpilogueDoneMsg()))
        asyncio.run(room.handle_action("p2", EpilogueDoneMsg()))
    assert room.state.phase == "ended"
    assert any("Kept: 0" in line for line in room.state.log)
    assert [c.id for c in room.state.epilogue_result.destroyed] == ["c1"]
    assert room.state.epilogue_result.favorite_card_ids == []


def test_record_vote_rejects_outsider_and_unknown_card() -> None:
    mgr = EpilogueManager(player_ids=["p1"])
    _start(mgr, [{"id": "c1", "title": "A", "description": "a"}])
    assert mgr.record_vote("spec-1", "c1", keep=True) is False
    assert mgr.record_vote("p1", "nope", keep=True) is False
    assert mgr.record_vote("p1", "c1", keep=True) is True
    assert mgr.to_dict()["votes"] == {"c1": {"p1": "keep"}}


def test_from_dict_sanitizes_votes_and_done_to_eligible_state() -> None:
    data = {
        "player_ids": ["p1", "p2"],
        "votes": {
            "c1": {"p1": "keep", "spec-1": "destroy"},
            "ghost-card": {"p1": "keep"},
        },
        "done": ["p1", "spec-1"],
        "cards": [{"id": "c1", "title": "A", "description": "a"}],
    }
    mgr = EpilogueManager.from_dict(data, AsyncMock())
    assert mgr.to_dict()["votes"] == {"c1": {"p1": "keep"}}
    assert mgr.to_dict()["done"] == ["p1"]
    assert mgr.all_done() is False


def _spectated_epilogue_room(host_is_spectator: bool = False) -> tuple[Room, AsyncMock]:
    room = Room("ABCDEF")
    if host_is_spectator:
        room.add_spectator("spec-1", "Watcher")
    room.add_player("p1", "Alice")
    room.add_player("p2", "Bob")
    if not host_is_spectator:
        room.add_spectator("spec-1", "Watcher")
    room.state = room.state.model_copy(
        update={"cards": {"c1": {"id": "c1", "title": "T", "description": "D", "origin": "authored"}}}
    )
    room.connections.connect("p1", AsyncMock())
    room.connections.connect("p2", AsyncMock())
    spec_ws = AsyncMock()
    room.connections.connect("spec-1", spec_ws)
    asyncio.run(room.start_epilogue())
    return room, spec_ws


def test_spectator_epilogue_vote_and_done_are_rejected() -> None:
    room, spec_ws = _spectated_epilogue_room()
    asyncio.run(room.handle_action("spec-1", EpilogueVoteMsg(card_id="c1", keep=False)))
    asyncio.run(room.handle_action("spec-1", EpilogueDoneMsg()))
    assert room.state.phase == "epilogue"
    assert room._epilogue.to_dict()["votes"]["c1"] == {}
    assert room._epilogue.to_dict()["done"] == []
    sent = [json.loads(c.args[0]) for c in spec_ws.send_text.call_args_list if c.args]
    assert sum(1 for m in sent if m["type"] == "error") >= 2


def test_spectator_host_cannot_vote_but_can_finalize() -> None:
    room, spec_ws = _spectated_epilogue_room(host_is_spectator=True)
    assert room.state.host_id == "spec-1"
    asyncio.run(room.handle_action("spec-1", EpilogueVoteMsg(card_id="c1", keep=True)))
    asyncio.run(room.handle_action("spec-1", EpilogueDoneMsg()))
    assert room.state.phase == "epilogue"
    sent = [json.loads(c.args[0]) for c in spec_ws.send_text.call_args_list if c.args]
    assert any(m["type"] == "error" for m in sent)

    with patch("agent.rag.store.upsert_card"), patch("agent.rag.store.delete_card"):
        asyncio.run(room.handle_action("p1", EpilogueVoteMsg(card_id="c1", keep=True)))
        asyncio.run(room.handle_action("spec-1", EpilogueFinalizeMsg()))
    assert room.state.phase == "ended"
    assert [c.id for c in room.state.epilogue_result.kept] == ["c1"]
    assert room.state.epilogue_result.favorite_card_ids == ["c1"]
