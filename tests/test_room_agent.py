"""Tests for Room agent-interpretation integration (run_agent mocked)."""

from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, patch

from agent.contract import InterpretResult, SnippetEffect
from models.effects import AddPointsOp, EffectProgram
from models.ws_messages import CreateCardMsg, Placement, PlayMsg, PreviewCardMsg
from board.rooms.room import Room


def _room() -> Room:
    r = Room("ABCDEF")
    r.add_player("p1", "Alice")
    r.add_player("p2", "Bob")
    r.state = r.state.model_copy(update={"phase": "playing"})
    return r


def test_play_interprets_and_applies_ok(monkeypatch) -> None:
    room = _room()
    # seed a card into state
    room.state = room.state.model_copy(
        update={"cards": {"c1": {"id": "c1", "title": "Gain 3", "description": "Gain 3 points."}}}
    )
    ws1 = AsyncMock()
    room.connections.connect("p1", ws1)
    room.connections.connect("p2", AsyncMock())

    fake_result = InterpretResult(
        program=EffectProgram(ops=[AddPointsOp(target="self", amount=3)]),
        snippet=None,
        verdict="ok",
    )
    with patch("agent.runtime.run_agent", return_value=fake_result):
        asyncio.run(room.handle_action("p1", PlayMsg(card_id="c1", placement=Placement(zone="self"))))

    # p1 gained 3 (add_points self resolves to actor), turn advanced to p2
    assert room.state.get_player("p1").score == 3
    assert room.state.turn_index == 1
    # a brewing message was broadcast at some point
    sent_types = [json.loads(c.args[0])["type"] for c in ws1.send_text.call_args_list]
    assert "brewing" in sent_types
    assert "card_interpreted" in sent_types


def test_play_unknown_card_errors(monkeypatch) -> None:
    room = _room()
    ws1 = AsyncMock()
    room.connections.connect("p1", ws1)
    with patch("agent.runtime.run_agent", return_value=InterpretResult(program=None, snippet=None, verdict="ok")):
        asyncio.run(room.handle_action("p1", PlayMsg(card_id="nope", placement=Placement(zone="self"))))
    sent = [json.loads(c.args[0]) for c in ws1.send_text.call_args_list]
    assert any(m["type"] == "error" for m in sent)


def test_create_card_interprets(monkeypatch) -> None:
    room = _room()
    room.connections.connect("p2", AsyncMock())
    fake_result = InterpretResult(program=None, snippet=None, verdict="invalid")
    with patch("agent.runtime.run_agent", return_value=fake_result):
        asyncio.run(room.handle_action("p2", CreateCardMsg(title="Wild", description="do stuff")))
    # a card was added and annotated with a verdict
    assert len(room.state.cards) == 1
    card = next(iter(room.state.cards.values()))
    assert card["verdict"] == "invalid"
    assert card["mechanical_status"] == "fallback"
    assert card["mechanical_reason"]
    assert card["correlation_id"]


def test_play_status_is_durable_in_snapshot() -> None:
    room = _room()
    room.state = room.state.model_copy(
        update={"cards": {"c1": {"id": "c1", "title": "Gain 3", "description": "Gain 3 points."}}}
    )
    room.connections.connect("p1", AsyncMock())
    room.connections.connect("p2", AsyncMock())
    result = InterpretResult(program=EffectProgram(ops=[AddPointsOp(target="self", amount=3)]), verdict="ok")

    with patch("agent.runtime.run_agent", return_value=result):
        asyncio.run(room.handle_action("p1", PlayMsg(card_id="c1")))

    card = room.snapshot()["cards"]["c1"]
    assert card["mechanical_status"] == "applied"
    assert card["mechanical_reason"] is None
    assert card["correlation_id"]


def test_preview_interprets_and_dry_runs_without_mutating_room() -> None:
    room = _room()
    ws = AsyncMock()
    room.connections.connect("p1", ws)
    before = room.state.model_dump()
    invalid_runtime = InterpretResult(
        snippet=SnippetEffect(
            code="def apply(state, ctx):\n    state.draw('self', 2)\n",
            explanation="uses a nonexistent method",
        ),
        verdict="ok",
    )

    with patch("agent.runtime.run_agent", return_value=invalid_runtime) as run:
        asyncio.run(room.handle_action("p1", PreviewCardMsg(title="Bad", description="Draw two")))

    assert room.state.model_dump() == before
    messages = [json.loads(call.args[0]) for call in ws.send_text.call_args_list]
    preview = next(message for message in messages if message["type"] == "preview_result")
    assert preview["mechanical_status"] == "rejected"
    assert "draw_cards" in preview["mechanical_reason"] or "draw" in preview["mechanical_reason"]
    assert preview["correlation_id"]
    assert run.call_args.kwargs["allow_persistent_tools"] is False
