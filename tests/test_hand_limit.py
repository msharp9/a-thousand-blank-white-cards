"""Rules.hand_limit enforcement (bead agd.4): the synthetic end-of-turn discard plan."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

import pytest

from engine.events import GameEvent, HookContext
from engine.reducers import apply_op
from models.effects import SetRuleOp
from models.game_state import GameState, Player
from models.ws_messages import InteractionResponseMsg
from board.rooms.room import Room


def _room(hand: list[str], limit: int | None, *, eliminated: bool = False) -> Room:
    room = Room("HANDLI")
    room.add_player("p1", "Alice")
    room.add_player("p2", "Bob")
    cards = {cid: {"id": cid, "title": cid.upper()} for cid in hand}
    players = [
        player.model_copy(update={"hand": list(hand), "eliminated": eliminated}) if player.id == "p1" else player
        for player in room.state.players
    ]
    room.state = room.state.model_copy(
        update={
            "phase": "playing",
            "cards": cards,
            "players": players,
            "deck": ["d1", "d2"],
            "rules": room.state.rules.model_copy(update={"hand_limit": limit}),
        }
    )
    room.connections.connect("p1", AsyncMock())
    room.connections.connect("p2", AsyncMock())
    return room


def _response(interaction_id: str, **payload) -> InteractionResponseMsg:
    return InteractionResponseMsg.model_validate(
        {"interaction_id": interaction_id, "payload": {"kind": "card_pick", **payload}}
    )


def test_over_limit_triggers_pick_for_exactly_the_excess() -> None:
    room = _room(["a1", "a2", "a3", "a4", "a5"], 3)

    async def scenario() -> None:
        await room._advance_turn()
        pending = room._pending_resolution
        assert pending is not None
        assert pending.purpose == "hand_limit"
        assert pending.request.kind == "card_pick"
        assert pending.request.from_hand is True
        assert pending.request.min_picks == 2
        assert pending.request.max_picks == 2
        assert pending.resolved_audience == ["p1"]
        assert room.state.active_player().id == "p1"  # advance suspended
        await room.handle_action("p1", _response(pending.interaction_id, card_ids=["a1", "a3"]))

    asyncio.run(scenario())
    assert room._pending_resolution is None
    assert room.state.get_player("p1").hand == ["a2", "a4", "a5"]
    assert {"a1", "a3"} <= set(room.state.discard)
    assert room.state.active_player().id == "p2"
    assert any("hand limit" in line for line in room.state.log)


def test_single_excess_uses_bare_card_id_pick() -> None:
    room = _room(["a1", "a2", "a3", "a4"], 3)

    async def scenario() -> None:
        await room._advance_turn()
        pending = room._pending_resolution
        assert pending is not None
        assert pending.request.min_picks == pending.request.max_picks == 1
        await room.handle_action("p1", _response(pending.interaction_id, card_id="a2"))

    asyncio.run(scenario())
    assert room.state.get_player("p1").hand == ["a1", "a3", "a4"]
    assert "a2" in room.state.discard
    assert room.state.active_player().id == "p2"


def test_timeout_auto_discards_from_hand_tail() -> None:
    room = _room(["a1", "a2", "a3", "a4", "a5"], 3)

    async def scenario() -> None:
        await room._advance_turn()
        assert room._pending_resolution is not None
        await room._resume_pending_resolution(timed_out=True)

    asyncio.run(scenario())
    assert room._pending_resolution is None
    assert room.state.get_player("p1").hand == ["a1", "a2", "a3"]
    assert {"a4", "a5"} <= set(room.state.discard)
    assert room.state.active_player().id == "p2"


def test_no_trigger_at_limit() -> None:
    room = _room(["a1", "a2", "a3"], 3)

    async def scenario() -> None:
        await room._advance_turn()

    asyncio.run(scenario())
    assert room._pending_resolution is None
    assert room.state.get_player("p1").hand == ["a1", "a2", "a3"]
    assert room.state.active_player().id == "p2"


def test_no_trigger_without_a_limit() -> None:
    room = _room(["a1", "a2", "a3", "a4", "a5", "a6"], None)

    async def scenario() -> None:
        await room._advance_turn()

    asyncio.run(scenario())
    assert room._pending_resolution is None
    assert len(room.state.get_player("p1").hand) == 6
    assert room.state.active_player().id == "p2"


def test_eliminated_active_player_is_skipped() -> None:
    room = _room(["a1", "a2", "a3", "a4", "a5"], 1, eliminated=True)

    async def scenario() -> None:
        await room._advance_turn()

    asyncio.run(scenario())
    assert room._pending_resolution is None
    assert room.state.get_player("p1").hand == ["a1", "a2", "a3", "a4", "a5"]
    assert room.state.active_player().id == "p2"


def test_short_pick_is_rejected() -> None:
    room = _room(["a1", "a2", "a3", "a4", "a5"], 3)

    async def scenario() -> None:
        await room._advance_turn()
        pending = room._pending_resolution
        await room.handle_action("p1", _response(pending.interaction_id, card_ids=["a1"]))
        assert room._pending_resolution is not None
        assert "p1" not in room._pending_resolution.responses

    asyncio.run(scenario())


class TestSetRulePath:
    def _state(self) -> GameState:
        return GameState(
            room_code="TEST",
            players=[Player(id="p1", name="A"), Player(id="p2", name="B")],
            phase="playing",
        )

    def _ctx(self) -> HookContext:
        return HookContext(event=GameEvent.ON_PLAY, actor_id="p1")

    def test_settable(self):
        out = apply_op(self._state(), SetRuleOp(path="hand_limit", value=5), self._ctx())
        assert out.rules.hand_limit == 5

    def test_liftable_with_none(self):
        state = apply_op(self._state(), SetRuleOp(path="hand_limit", value=5), self._ctx())
        out = apply_op(state, SetRuleOp(path="hand_limit", value=None), self._ctx())
        assert out.rules.hand_limit is None

    def test_negative_rejected(self):
        with pytest.raises(ValueError):
            apply_op(self._state(), SetRuleOp(path="hand_limit", value=-1), self._ctx())
