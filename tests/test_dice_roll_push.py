"""Room-level dice_roll immediacy push (board.rooms.room._push_dice_rolls).

Each roll_die resolved by a play gets exactly ONE dice_roll broadcast, driven
by diffing dice_roll history events against the pre-play sequence baseline —
so later plays never re-push earlier rolls.
"""

from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock

from models.ws_messages import PlayMsg
from board.rooms.room import Room


def _card(cid: str, title: str, ops: list[dict]) -> dict:
    return {
        "id": cid,
        "title": title,
        "description": f"{title}.",
        "canonical": {"target": "self", "placement": "discard", "venue": "all", "ops": ops},
    }


def _dice_card(cid: str = "dice", *, count: int = 2, rolls: int = 1) -> dict:
    op = {"op": "roll_die", "args": {"sides": 6, "count": count, "outcome": "add_points"}}
    return _card(cid, "Lucky Toss", [op] * rolls)


def _room(*cards: dict) -> Room:
    room = Room("ABCDEF")
    for pid, name in (("p1", "Alice"), ("p2", "Bob")):
        room.add_player(pid, name)
    players = [
        room.state.players[0].model_copy(update={"hand": [c["id"] for c in cards]}),
        room.state.players[1],
    ]
    room.state = room.state.model_copy(
        update={
            "phase": "playing",
            "deck": ["d1", "d2", "d3"],
            "cards": {c["id"]: c for c in cards},
            "players": players,
        }
    )
    room._has_drawn = True
    return room


def _connect_all(room: Room) -> dict[str, AsyncMock]:
    socks = {}
    for pid in ("p1", "p2"):
        socks[pid] = AsyncMock()
        room.connections.connect(pid, socks[pid])
    return socks


def _dice_msgs(sock: AsyncMock) -> list[dict]:
    sent = [json.loads(c.args[0]) for c in sock.send_text.call_args_list]
    return [m for m in sent if m["type"] == "dice_roll"]


def test_one_push_per_roll_broadcast_to_everyone() -> None:
    room = _room(_dice_card())
    socks = _connect_all(room)
    asyncio.run(room.handle_action("p1", PlayMsg(card_id="dice")))

    events = [e for e in room.state.history_events if e.kind == "dice_roll"]
    assert len(events) == 1
    for sock in socks.values():
        msgs = _dice_msgs(sock)
        assert len(msgs) == 1
        msg = msgs[0]
        assert msg["actor_id"] == "p1"
        assert msg["card_id"] == "dice"
        assert msg["sides"] == 6
        assert len(msg["values"]) == 2
        assert all(1 <= v <= 6 for v in msg["values"])
        assert msg["total"] == sum(msg["values"])
        assert msg == {
            "type": "dice_roll",
            "actor_id": "p1",
            "sides": 6,
            "values": events[0].data["values"],
            "total": events[0].data["total"],
            "card_id": "dice",
        }
    assert room.state.get_player("p1").score == events[0].data["total"]


def test_two_roll_ops_push_twice() -> None:
    room = _room(_dice_card(rolls=2))
    socks = _connect_all(room)
    asyncio.run(room.handle_action("p1", PlayMsg(card_id="dice")))
    assert len(_dice_msgs(socks["p2"])) == 2


def test_later_play_does_not_repush_old_rolls() -> None:
    dice = _dice_card()
    plain = _card("plain", "Nothing Fancy", [{"op": "add_points", "args": {"target": "self", "amount": 1}}])
    room = _room(dice)

    async def scenario() -> None:
        socks = _connect_all(room)
        await room.handle_action("p1", PlayMsg(card_id="dice"))
        # p2 is now active; hand them a diceless card and play it.
        players = [
            room.state.players[0],
            room.state.players[1].model_copy(update={"hand": [*room.state.players[1].hand, "plain"]}),
        ]
        room.state = room.state.model_copy(update={"players": players, "cards": {**room.state.cards, "plain": plain}})
        before = {pid: len(_dice_msgs(sock)) for pid, sock in socks.items()}
        await room.handle_action("p2", PlayMsg(card_id="plain"))
        for pid, sock in socks.items():
            assert len(_dice_msgs(sock)) == before[pid] == 1

    asyncio.run(scenario())
