"""Dynamic phase C — hooks-as-data fire through the Room's per-room EventBus."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

from board.rooms.room import Room
from models.effects import DrawCardsOp, EffectProgram
from models.ws_messages import PlayMsg

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
        await room.handle_action("p2", PlayMsg(card_id="blue1"))

    asyncio.run(_p2_tries_blue_then_red())
    assert "blue1" in room.state.get_player("p2").hand
    assert room.state.active_player().id == "p2"
    assert any("rejected" in line for line in room.state.log)

    asyncio.run(room.handle_action("p2", PlayMsg(card_id="red1")))
    assert "red1" not in room.state.get_player("p2").hand
    assert room.state.active_player().id == "p1"


HAND_SCORER = "def apply(state, ctx):\n    state.add_points('self', len(state.my_hand()))\n"


def test_chess_shape_snippet_reads_hand_and_scores() -> None:
    from unittest.mock import patch

    from agent.contract import InterpretResult, SnippetEffect

    chess = {"id": "chess", "title": "Chess", "description": "Draw 2, score per card in hand.", "creator_id": "p1"}
    room = _room({"chess": chess}, {"p1": ["chess", "x1", "x2"]}, deck=["d1", "d2", "d3"])
    result = InterpretResult(
        program=EffectProgram(ops=[DrawCardsOp(target="self", amount=2)]),
        snippet=SnippetEffect(code=HAND_SCORER, explanation="draw then score per hand card"),
        verdict="ok",
        comment="Chess, sure.",
    )
    with patch("agent.runtime.run_agent", return_value=result):
        asyncio.run(room.handle_action("p1", PlayMsg(card_id="chess")))

    assert room.state.get_player("p1").score == 4
    assert len(room.state.get_player("p1").hand) == 4


def test_triggered_snippet_interpretation_registers_hook_via_op_pipeline() -> None:
    from unittest.mock import patch

    from agent.contract import InterpretResult, SnippetEffect

    card = {"id": "tax", "title": "Alice Tax", "description": "Every turn Alice gains 1.", "creator_id": "p1"}
    room = _room({"tax": card}, {"p1": ["tax"]}, deck=["d1", "d2", "d3"])
    result = InterpretResult(
        program=None,
        snippet=SnippetEffect(code=HOOK_CODE, explanation="alice tax", trigger="on_turn_start"),
        verdict="ok",
        comment="A tax!",
    )
    with patch("agent.runtime.run_agent", return_value=result):
        asyncio.run(room.handle_action("p1", PlayMsg(card_id="tax")))

    assert len(room.state.hooks) == 1
    assert room.state.hooks[0].event == "on_turn_start"
    assert room.state.hooks[0].code == HOOK_CODE
    # The card's canonical now carries the register_hook op — a kept copy
    # replays deterministically in a future game.
    assert room.state.cards["tax"]["canonical"]["ops"][0]["op"] == "register_hook"


def test_kept_hook_card_replays_deterministically_next_game() -> None:
    # Simulate game 2: a card whose canonical carries register_hook (as the RAG
    # round-trip stores it) is played — no LLM involved, hook registers, fires.
    kept = {
        "id": "kept1",
        "title": "Alice Tax",
        "description": "Every turn Alice gains 1.",
        "origin": "authored",
        "canonical": {"ops": [{"op": "register_hook", "args": {"event": "on_turn_start", "code": HOOK_CODE}}]},
    }
    room = _room({"kept1": kept}, {"p1": ["kept1"]}, deck=["d1", "d2"])
    asyncio.run(room.handle_action("p1", PlayMsg(card_id="kept1")))
    assert room.state.hooks and room.state.hooks[0].event == "on_turn_start"
    assert room.state.get_player("p1").score == 1  # fired on p2's turn start


BAD_HOOK_CODE = "def apply(state, ctx):\n    1 / 0\n"


def test_crashing_hook_snippet_reports_hook_failure_to_eval_agent(monkeypatch) -> None:
    from unittest.mock import MagicMock

    from config import get_settings

    monkeypatch.setenv("EVAL_AGENT_ENABLED", "true")
    get_settings.cache_clear()
    spy = MagicMock()
    monkeypatch.setattr("evals.effect_failure_agent.schedule_effect_failure_report", spy)

    bad = {
        "id": "badh",
        "title": "Cursed Rule",
        "description": "Crashes every turn.",
        "canonical": {"ops": [{"op": "register_hook", "args": {"event": "on_turn_start", "code": BAD_HOOK_CODE}}]},
    }
    room = _room({"badh": bad}, {"p1": ["badh"]}, deck=["d1", "d2", "d3"])

    # Playing registers the hook; the turn advance fires ON_TURN_START, whose
    # snippet crashes — drained by _emit_hooks and reported as hook_failure.
    asyncio.run(room.handle_action("p1", PlayMsg(card_id="badh")))

    assert spy.called
    payload = spy.call_args.args[0]
    assert payload.kind == "hook_failure"
    assert payload.card_id == "badh"
    assert "division by zero" in (payload.exception or "")
    # the game kept going: turn advanced to p2 despite the broken hook
    assert room.state.active_player().id == "p2"


def test_epilogue_upsert_carries_structured_canonical(monkeypatch) -> None:
    import agent.rag.store as rag_store
    from board.rooms.epilogue import EpilogueManager

    captured: dict = {}

    def fake_upsert(card_id, *, title, description, canonical, source, keep_votes=0, destroy_votes=0, art=None):
        captured[card_id] = canonical

    monkeypatch.setattr(rag_store, "upsert_card", fake_upsert)
    monkeypatch.setattr(rag_store, "get_card_totals", lambda cid: None)

    from board.rooms.connections import ConnectionManager

    mgr = EpilogueManager(player_ids=["p1"])
    mgr._connections = ConnectionManager()
    mgr._cards = [
        {
            "id": "c1",
            "title": "T",
            "description": "D",
            "canonical": {"ops": [{"op": "register_hook", "args": {"event": "on_play", "code": HOOK_CODE}}]},
        }
    ]
    mgr._votes = {"c1": {"p1": "keep"}}
    asyncio.run(mgr.tally_and_persist())

    import json as _json

    assert _json.loads(captured["c1"])["ops"][0]["op"] == "register_hook"
