"""Dynamic phase C — hooks-as-data fire through the Room's per-room EventBus."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

from models.ws_messages import DrawMsg, PlayMsg
from board.rooms.room import Room

HOOK_CODE = "def apply(state, ctx):\n    state.add_points('id:p1', 1)\n"


def _room(cards: dict, hands: dict[str, list[str]], deck: list[str]) -> Room:
    r = Room("ABCDEF")
    r.add_player("p1", "Alice")
    r.add_player("p2", "Bob")
    players = [p.model_copy(update={"hand": hands.get(p.id, [])}) for p in r.state.players]
    r.state = r.state.model_copy(update={"phase": "playing", "deck": deck, "cards": cards, "players": players})
    r._has_drawn = True
    r.connections.connect("p1", AsyncMock())
    r.connections.connect("p2", AsyncMock())
    return r


def _hook_card() -> dict:
    return {
        "id": "hookc",
        "title": "Alice Tax",
        "description": "At the start of every turn, Alice gains 1 point.",
        "canonical": {"ops": [{"op": "register_hook", "args": {"event": "on_turn_start", "code": HOOK_CODE}}]},
    }


def test_played_card_registers_serialized_hook_that_fires_on_turn_start() -> None:
    room = _room({"hookc": _hook_card()}, {"p1": ["hookc"]}, deck=["d1", "d2", "d3"])

    asyncio.run(room.handle_action("p1", PlayMsg(card_id="hookc")))

    assert len(room.state.hooks) == 1
    assert room.state.hooks[0].event == "on_turn_start"
    # Playing advanced the turn to p2; ON_TURN_START fired for that turn.
    assert room.state.get_player("p1").score == 1


def test_hooks_do_not_leak_across_rooms() -> None:
    room_a = _room({"hookc": _hook_card()}, {"p1": ["hookc"]}, deck=["d1", "d2"])
    room_b = _room({}, {}, deck=["d1", "d2"])

    asyncio.run(room_a.handle_action("p1", PlayMsg(card_id="hookc")))
    assert room_a.state.hooks

    async def _turn_in_b() -> None:
        await room_b.handle_action("p1", DrawMsg())
        await room_b.handle_action("p1", PlayMsg(card_id="nope"))

    asyncio.run(_turn_in_b())
    assert room_b.state.hooks == []
    assert room_b.state.get_player("p1").score == 0


def test_hooks_survive_store_round_trip(tmp_path) -> None:
    from board.rooms.store import FileRoomStore

    store = FileRoomStore(tmp_path)
    room = _room({"hookc": _hook_card()}, {"p1": ["hookc"]}, deck=["d1", "d2"])
    asyncio.run(room.handle_action("p1", PlayMsg(card_id="hookc")))
    store.put(room.code, room)

    got = FileRoomStore(tmp_path).get(room.code)
    assert got is not None
    assert [h.id for h in got.state.hooks] == [h.id for h in room.state.hooks]
    assert got.state.hooks[0].code == HOOK_CODE
