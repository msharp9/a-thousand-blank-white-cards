"""Room-level dice_roll immediacy push (board.rooms.room._push_dice_rolls).

Each roll_die recorded in history — by a play's plan, a reaction card's own
effects, or a lifecycle hook — gets exactly ONE dice_roll broadcast, driven
by the room's ``_dice_seq_pushed`` watermark, so later pushes never re-push
earlier rolls.
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


def test_reaction_card_roll_is_pushed() -> None:
    # A reaction card's OWN roll_die resolves inside _execute_reaction, before
    # the suspended play commits — it must still get its immediacy push.
    plain = _card("atk", "Zap", [{"op": "add_points", "args": {"target": "self", "amount": 5}}])
    reaction = {
        "id": "rx",
        "title": "Lucky Counter",
        "description": "Counter and roll.",
        "canonical": {
            "target": "self",
            "placement": "discard",
            "venue": "all",
            "trigger": "on_reaction",
            "ops": [
                {"op": "counter_play", "args": {"mode": "negate"}},
                {"op": "roll_die", "args": {"sides": 6, "count": 1, "outcome": "add_points"}},
            ],
        },
    }
    room = _room(plain)
    players = [
        room.state.players[0],
        room.state.players[1].model_copy(update={"hand": ["rx"]}),
    ]
    room.state = room.state.model_copy(update={"players": players, "cards": {**room.state.cards, "rx": reaction}})

    async def scenario() -> None:
        socks = _connect_all(room)
        await room.handle_action("p1", PlayMsg(card_id="atk"))
        assert room._pending is not None
        await room.handle_action("p2", PlayMsg(card_id="rx", as_reaction=True))
        events = [e for e in room.state.history_events if e.kind == "dice_roll"]
        assert len(events) == 1
        for sock in socks.values():
            msgs = _dice_msgs(sock)
            assert len(msgs) == 1
            assert msgs[0]["actor_id"] == "p2"
            assert msgs[0]["card_id"] == "rx"
            assert msgs[0]["values"] == events[0].data["values"]
        assert room.state.get_player("p2").score == events[0].data["total"]
        assert room.state.get_player("p1").score == 0  # play was countered

    asyncio.run(scenario())


def test_turn_start_hook_roll_is_pushed() -> None:
    # A persistent hook rolling outside any play tail (_emit_hooks at turn
    # start) must broadcast its roll too, not just record it in history.
    hook_code = "def apply(state, ctx):\n    state.roll_die(sides=6, count=1, target='id:p2', outcome='add_points')\n"
    hook_card = _card(
        "hookc", "Dice Tax", [{"op": "register_hook", "args": {"event": "on_turn_start", "code": hook_code}}]
    )
    room = _room(hook_card)

    async def scenario() -> None:
        socks = _connect_all(room)
        # Playing the hook card advances the turn; ON_TURN_START fires for p2.
        await room.handle_action("p1", PlayMsg(card_id="hookc"))
        events = [e for e in room.state.history_events if e.kind == "dice_roll"]
        assert len(events) == 1
        for sock in socks.values():
            msgs = _dice_msgs(sock)
            assert len(msgs) == 1
            assert msgs[0]["values"] == events[0].data["values"]
        assert room.state.get_player("p2").score == events[0].data["total"]

    asyncio.run(scenario())


def test_restored_room_does_not_replay_old_rolls(tmp_path) -> None:
    from board.rooms.store import FileRoomStore

    room = _room(_dice_card())
    asyncio.run(room.handle_action("p1", PlayMsg(card_id="dice")))
    store = FileRoomStore(tmp_path)
    store.put(room.code, room)

    restored = FileRoomStore(tmp_path).get(room.code)
    assert restored is not None
    plain = _card("plain", "Nothing Fancy", [{"op": "add_points", "args": {"target": "self", "amount": 1}}])

    async def scenario() -> None:
        socks = {}
        for pid in ("p1", "p2"):
            socks[pid] = AsyncMock()
            restored.connections.connect(pid, socks[pid])
        players = [
            restored.state.players[0],
            restored.state.players[1].model_copy(update={"hand": [*restored.state.players[1].hand, "plain"]}),
        ]
        restored.state = restored.state.model_copy(
            update={"players": players, "cards": {**restored.state.cards, "plain": plain}}
        )
        await restored.handle_action("p2", PlayMsg(card_id="plain"))
        for sock in socks.values():
            assert _dice_msgs(sock) == []

    asyncio.run(scenario())
