"""Tests for Room agent-interpretation integration (run_agent mocked)."""

from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

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


def test_create_card_mid_game_rejected_and_never_interpreted() -> None:
    # Authoring is setup-only: create_card during the playing phase gets the
    # standard error envelope, no card is registered, and the agent never runs.
    room = _room()
    ws2 = AsyncMock()
    room.connections.connect("p2", ws2)
    with patch("agent.runtime.run_agent") as run:
        asyncio.run(room.handle_action("p2", CreateCardMsg(title="Wild", description="do stuff")))
    run.assert_not_called()
    assert room.state.cards == {}
    sent = [json.loads(c.args[0]) for c in ws2.send_text.call_args_list]
    assert any(m["type"] == "error" for m in sent)


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


# ── failure-triggered eval-agent reporting ──
# _report_effect_failure imports schedule_effect_failure_report lazily, so the
# spy patches the source module attribute (evals.effect_failure_agent); the
# room-side eval_agent_enabled gate is what these tests exercise.


def _eval_room(monkeypatch, spy, *, enabled: bool = True) -> Room:
    from config import get_settings

    monkeypatch.setenv("EVAL_AGENT_ENABLED", "true" if enabled else "false")
    get_settings.cache_clear()
    monkeypatch.setattr("evals.effect_failure_agent.schedule_effect_failure_report", spy)
    room = _room()
    room.state = room.state.model_copy(
        update={"cards": {"c1": {"id": "c1", "title": "Weird", "description": "Do something impossible."}}}
    )
    room.connections.connect("p1", AsyncMock())
    room.connections.connect("p2", AsyncMock())
    return room


def test_invalid_verdict_reports_and_play_still_completes(monkeypatch) -> None:
    spy = MagicMock()
    room = _eval_room(monkeypatch, spy)
    result = InterpretResult(program=None, snippet=None, verdict="invalid", comment="nope")
    with patch("agent.runtime.run_agent", return_value=result):
        asyncio.run(room.handle_action("p1", PlayMsg(card_id="c1")))

    spy.assert_called_once()
    payload = spy.call_args.args[0]
    assert payload.kind == "invalid_verdict"
    assert payload.card_id == "c1"
    assert payload.verdict == "invalid"
    assert payload.comment == "nope"
    # the play completed via the CustomNote fallback and the turn advanced
    assert room.state.cards["c1"]["mechanical_status"] == "fallback"
    assert room.state.turn_index == 1


def test_ok_verdict_with_empty_plan_reports_no_op(monkeypatch) -> None:
    spy = MagicMock()
    room = _eval_room(monkeypatch, spy)
    result = InterpretResult(program=None, snippet=None, verdict="ok", comment="sure")
    with patch("agent.runtime.run_agent", return_value=result):
        asyncio.run(room.handle_action("p1", PlayMsg(card_id="c1")))

    spy.assert_called_once()
    payload = spy.call_args.args[0]
    assert payload.kind == "no_op"
    assert payload.verdict == "ok"
    assert payload.run_metrics is not None  # UsageCallback snapshot captured for the eval agent
    assert room.state.turn_index == 1


def test_plan_execution_failure_reports_sandbox_failure(monkeypatch) -> None:
    spy = MagicMock()
    room = _eval_room(monkeypatch, spy)

    async def boom(self, base_state, plan, ctx, card, **kwargs):
        raise RuntimeError("sandbox blew up")

    monkeypatch.setattr(Room, "_execute_plan", boom)
    result = InterpretResult(program=EffectProgram(ops=[AddPointsOp(target="self", amount=3)]), verdict="ok")
    with patch("agent.runtime.run_agent", return_value=result):
        asyncio.run(room.handle_action("p1", PlayMsg(card_id="c1")))

    spy.assert_called_once()
    payload = spy.call_args.args[0]
    assert payload.kind == "sandbox_failure"
    assert "sandbox blew up" in (payload.exception or "")
    assert room.state.cards["c1"]["mechanical_status"] == "fallback"
    assert any("[snippet error]" in line for line in room.state.log)


def test_reports_dedupe_per_card_and_kind(monkeypatch) -> None:
    spy = MagicMock()
    room = _eval_room(monkeypatch, spy)
    card = room.state.cards["c1"]
    room._report_effect_failure("no_op", card, "corr-1", verdict="ok")
    room._report_effect_failure("no_op", card, "corr-2", verdict="ok")
    spy.assert_called_once()
    room._report_effect_failure("sandbox_failure", card, "corr-3", exc=RuntimeError("x"))
    assert spy.call_count == 2


def test_disabled_eval_agent_never_reports(monkeypatch) -> None:
    spy = MagicMock()
    room = _eval_room(monkeypatch, spy, enabled=False)
    result = InterpretResult(program=None, snippet=None, verdict="invalid")
    with patch("agent.runtime.run_agent", return_value=result):
        asyncio.run(room.handle_action("p1", PlayMsg(card_id="c1")))

    spy.assert_not_called()
    room._report_effect_failure("no_op", room.state.cards["c1"], "corr-x")
    spy.assert_not_called()


def test_preview_interprets_and_dry_runs_without_mutating_room() -> None:
    # preview_card is setup-only (like create_card), so preview from setup.
    room = _room()
    room.state = room.state.model_copy(update={"phase": "setup"})
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
