"""Tests for Room agent-interpretation integration (run_agent mocked)."""

from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, patch

from agent.contract import InterpretResult
from models.effects import AddPointsOp, EffectProgram
from models.ws_messages import CreateCardMsg, Placement, PlayMsg
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


def test_interpret_card_adapter_shape() -> None:
    from unittest.mock import patch

    import agent.graph as g
    from agent.schemas import Verdict

    canned = {
        "program": None,
        "snippet": None,
        "verdict": Verdict(intent=True, timing=True, target=True, trigger=True, magnitude=True, ok=True, reason="ok"),
    }
    with patch.object(g.graph, "invoke", return_value=canned):
        out = g.interpret_card("t", "d")
    assert out["verdict"] == "ok"
