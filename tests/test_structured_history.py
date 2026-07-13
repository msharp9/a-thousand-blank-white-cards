from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock

from agent.tools.read_game_history import make_read_game_history_tool
from board.rooms.room import Room
from board.rooms.store import FileRoomStore
from engine.apply import apply_effect
from engine.events import GameEvent, HookContext
from engine.history import append_history_event, draw_totals
from engine.sandbox.revalidate import apply_snippet_diff
from engine.sandbox.runner import execute_snippet
from models.effects import AddPointsOp, ChangeDrawCountOp, DrawCardsOp, EffectProgram
from models.game_state import GameState, Player
from models.ws_messages import DrawMsg, PlayMsg


def _state() -> GameState:
    return GameState(
        room_code="HISTORY",
        players=[Player(id="p1", name="Alice"), Player(id="p2", name="Bob")],
        deck=["d1", "d2", "d3"],
        phase="playing",
    )


def _ctx() -> HookContext:
    return HookContext(event=GameEvent.ON_PLAY, actor_id="p1", card_id="public-card")


def test_effect_ops_record_actual_draw_score_and_rule_changes() -> None:
    state = apply_effect(
        _state(),
        EffectProgram(
            ops=[
                DrawCardsOp(target="all", amount=2),
                AddPointsOp(target="self", amount=4),
                ChangeDrawCountOp(amount=0),
            ]
        ),
        _ctx(),
    )

    assert [(event.kind, event.target_player_ids, event.amount) for event in state.history_events] == [
        ("draw", ["p1"], 2),
        ("draw", ["p2"], 1),
        ("score_change", ["p1"], 4),
        ("rule_change", [], None),
    ]
    assert draw_totals(state) == {"p1": 2, "p2": 1}


def test_no_actual_change_records_no_event() -> None:
    state = _state().model_copy(update={"deck": []})

    state = apply_effect(
        state,
        EffectProgram(
            ops=[
                DrawCardsOp(target="self", amount=2),
                AddPointsOp(target="self", amount=0),
                ChangeDrawCountOp(amount=1),
            ]
        ),
        _ctx(),
    )

    assert state.history_events == []


def test_room_turn_draw_records_once_and_snapshot_reconnects() -> None:
    room = Room("HISTORY")
    room.add_player("p1", "Alice")
    room.add_player("p2", "Bob")
    room.state = room.state.model_copy(update={"deck": ["d1", "d2"], "phase": "playing"})
    room.connections.connect("p1", AsyncMock())

    asyncio.run(room.handle_action("p1", DrawMsg()))

    draws = [event for event in room.state.history_events if event.kind == "draw"]
    assert len(draws) == 1
    assert draws[0].amount == 1
    assert room.snapshot()["history_events"] == [draws[0].model_dump()]


def test_room_play_and_game_end_are_each_recorded_once() -> None:
    room = Room("HISTORY")
    room.add_player("p1", "Alice")
    room.add_player("p2", "Bob")
    card = {
        "id": "finish",
        "title": "We Are Done",
        "description": "You win now.",
        "canonical": {
            "timing": "immediate",
            "target": "self",
            "placement": "self",
            "ops": [{"op": "end_game", "args": {"winner": "self"}}],
        },
    }
    players = [room.state.get_player("p1").model_copy(update={"hand": ["finish"]}), room.state.get_player("p2")]
    room.state = room.state.model_copy(
        update={"players": players, "cards": {"finish": card}, "deck": ["d1"], "phase": "playing"}
    )
    room._has_drawn = True

    asyncio.run(room.handle_action("p1", PlayMsg(card_id="finish")))

    assert room.state.phase == "results"
    assert room.state.winner_ids == ["p1"]
    assert len([event for event in room.state.history_events if event.kind == "play"]) == 1
    game_end = [event for event in room.state.history_events if event.kind == "game_end"]
    assert len(game_end) == 1
    assert game_end[0].target_player_ids == ["p1"]


def test_cannot_play_draw_and_snippet_draw_record_exact_amounts() -> None:
    room = Room("HISTORY")
    room.add_player("p1", "Alice")
    room.add_player("p2", "Bob")
    room.state = room.state.model_copy(update={"deck": ["d1", "d2"], "phase": "playing"})

    asyncio.run(room._apply_cannot_play("p1"))
    state = apply_snippet_diff(
        room.state,
        [{"op": "draw_cards", "target": "self", "amount": 5}],
        _ctx(),
        origin="hook",
    )

    assert [event.amount for event in state.history_events if event.kind == "draw"] == [1, 1]


def test_history_tool_and_sandbox_never_expose_drawn_card_ids() -> None:
    state = apply_effect(_state(), EffectProgram(ops=[DrawCardsOp(target="self", amount=2)]), _ctx())
    tool_payload = json.loads(make_read_game_history_tool(state).invoke({"aggregate": "events"}))

    encoded = json.dumps(tool_payload)
    assert "d1" not in encoded
    assert "d2" not in encoded
    assert tool_payload["events"][0]["amount"] == 2

    sandbox_ops = execute_snippet(
        "def apply(state, ctx):\n    state.custom_note(str(state.draw_totals()['p1']))\n",
        json.loads(state.model_dump_json()),
        {"actor_id": "p1", "event": "on_play"},
    )
    assert sandbox_ops == [{"op": "custom_note", "note": "2"}]


def test_most_cards_drawn_snippet_sets_all_tied_winner_overrides() -> None:
    state = _state()
    for player_id, amount in (("p1", 3), ("p2", 3)):
        state = append_history_event(
            state,
            "draw",
            actor_id=player_id,
            target_player_ids=[player_id],
            amount=amount,
        )
    code = (
        "def apply(state, ctx):\n"
        "    totals = state.draw_totals()\n"
        "    best = max(totals.values())\n"
        "    winners = []\n"
        "    for player_id, total in totals.items():\n"
        "        if total == best:\n"
        "            winners.append('id:' + player_id)\n"
        "    state.end_game(winners)\n"
    )
    raw_ops = execute_snippet(code, json.loads(state.model_dump_json()), {"actor_id": "p1", "event": "on_play"})

    resolved = apply_snippet_diff(state, raw_ops, _ctx())

    assert resolved.winner_override == ["p1", "p2"]


def test_history_persists_through_file_store_without_duplication(tmp_path) -> None:
    room = Room("HISTORY")
    room.add_player("p1", "Alice")
    room.state = append_history_event(room.state, "play", actor_id="p1", card_id="public-card")
    store = FileRoomStore(tmp_path)
    store.put(room.code, room)

    loaded = FileRoomStore(tmp_path).get(room.code)

    assert loaded is not None
    assert loaded.state.history_events == room.state.history_events
    assert len(loaded.state.history_events) == 1
