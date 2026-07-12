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


VALIDATE_COLOR_MATCH = (
    "def apply(state, ctx):\n"
    "    color = ctx.get('card_attributes', {}).get('color')\n"
    "    if color != 'red':\n"
    "        state.reject_play('only red cards may be played')\n"
)


def _validation_room() -> Room:
    red = {"id": "red1", "title": "Red Card", "description": "x", "attributes": {"color": "red"}}
    blue = {"id": "blue1", "title": "Blue Card", "description": "x", "attributes": {"color": "blue"}}
    rule = {
        "id": "rulec",
        "title": "Color Law",
        "description": "Only red cards may be played.",
        "canonical": {
            "ops": [{"op": "register_hook", "args": {"event": "on_validate_play", "code": VALIDATE_COLOR_MATCH}}]
        },
    }
    return _room(
        {"red1": red, "blue1": blue, "rulec": rule},
        {"p1": ["rulec"], "p2": ["blue1", "red1"]},
        deck=["d1", "d2", "d3"],
    )


def test_validate_play_hook_vetoes_and_returns_card() -> None:
    room = _validation_room()
    asyncio.run(room.handle_action("p1", PlayMsg(card_id="rulec")))
    assert any(h.event == "on_validate_play" for h in room.state.hooks)

    async def _p2_tries_blue_then_red() -> None:
        await room.handle_action("p2", DrawMsg())
        await room.handle_action("p2", PlayMsg(card_id="blue1"))

    asyncio.run(_p2_tries_blue_then_red())
    assert "blue1" in room.state.get_player("p2").hand
    assert room.state.active_player().id == "p2"
    assert any("rejected" in line for line in room.state.log)

    asyncio.run(room.handle_action("p2", PlayMsg(card_id="red1")))
    assert "red1" not in room.state.get_player("p2").hand
    assert room.state.active_player().id == "p1"


HAND_SCORER = (
    "def apply(state, ctx):\n    state.draw_cards('self', 2)\n    state.add_points('self', len(state.my_hand()))\n"
)


def test_chess_shape_snippet_reads_hand_and_scores() -> None:
    from unittest.mock import patch

    from agent.contract import InterpretResult, SnippetEffect

    chess = {"id": "chess", "title": "Chess", "description": "Draw 2, score per card in hand.", "creator_id": "p1"}
    room = _room({"chess": chess}, {"p1": ["chess", "x1", "x2"]}, deck=["d1", "d2", "d3"])
    result = InterpretResult(
        program=None,
        snippet=SnippetEffect(code=HAND_SCORER, explanation="draw then score per hand card"),
        verdict="ok",
        comment="Chess, sure.",
    )
    with patch("agent.runtime.run_agent", return_value=result):
        asyncio.run(room.handle_action("p1", PlayMsg(card_id="chess")))

    # Hand at snippet time: chess, x1, x2 -> +3 points; then draw 2 and the
    # played chess leaves the hand: 3 + 2 - 1 = 4 cards.
    assert room.state.get_player("p1").score == 3
    assert len(room.state.get_player("p1").hand) == 4
