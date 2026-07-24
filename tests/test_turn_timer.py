"""Pausable turn timer (bead agd.1): rules.turn_timer + Room.TurnTimer.

Timer scenarios run inside one asyncio.run(...) so the clock's task lives on a
single event loop; scenarios cancel the clock before the loop closes.
"""

from __future__ import annotations

import asyncio
import json
import time
from unittest.mock import AsyncMock

import pytest

from engine.events import GameEvent, HookContext
from engine.reducers import apply_op
from models.effects import AddPointsOp, OpsStep, ResolutionPlan, SetRuleOp
from models.game_state import GameState, Player
from models.ws_messages import EndTurnMsg, InteractionResponseMsg, PassReactionMsg, PlayMsg
from board.rooms.manager import RoomManager
from board.rooms.room import Room, TurnTimer


def _room(*, turn_timer: int | None = 30, hand_limit: int | None = None, p1_hand: list[str] | None = None) -> Room:
    room = Room("TIMERS")
    room.add_player("p1", "Alice")
    room.add_player("p2", "Bob")
    hand = p1_hand or []
    cards = {cid: {"id": cid, "title": cid.upper()} for cid in hand}
    players = [
        player.model_copy(update={"hand": list(hand)}) if player.id == "p1" else player for player in room.state.players
    ]
    room.state = room.state.model_copy(
        update={
            "phase": "playing",
            "cards": cards,
            "players": players,
            "deck": ["d1", "d2", "d3"],
            "rules": room.state.rules.model_copy(update={"turn_timer": turn_timer, "hand_limit": hand_limit}),
        }
    )
    return room


def _connect(room: Room, *player_ids: str) -> dict[str, AsyncMock]:
    socks = {}
    for pid in player_ids:
        socks[pid] = AsyncMock()
        room.connections.connect(pid, socks[pid])
    return socks


def _timer_msgs(sock: AsyncMock) -> list[dict]:
    sent = [json.loads(c.args[0]) for c in sock.send_text.call_args_list]
    return [m for m in sent if m["type"] == "turn_timer"]


# ── TurnTimer accounting ────────────────────────────────────────────────────


def test_start_pause_resume_accounting() -> None:
    async def scenario() -> None:
        timer = TurnTimer(AsyncMock())
        timer.start(10, "p1")
        assert timer.running and not timer.paused
        assert timer.player_id == "p1"
        deadline = timer.deadline_epoch_ms
        assert deadline is not None
        assert 9_000 <= deadline - time.time() * 1000 <= 10_000

        assert timer.pause() is True
        assert timer.paused and not timer.running
        assert timer.deadline_epoch_ms is None
        banked = timer._remaining
        assert banked is not None and 9.0 <= banked <= 10.0

        await asyncio.sleep(0.05)
        assert timer.resume() is True
        assert timer.running and not timer.paused
        resumed = timer.deadline_epoch_ms
        # Re-armed with the banked remainder, not the original duration.
        assert resumed is not None
        assert abs((resumed - time.time() * 1000) / 1000 - banked) < 0.5
        timer.cancel()
        assert not timer.running and not timer.paused and timer.player_id is None

    asyncio.run(scenario())


def test_pause_and_resume_noop_when_inactive() -> None:
    async def scenario() -> None:
        timer = TurnTimer(AsyncMock())
        assert timer.pause() is False
        assert timer.resume() is False
        timer.start(5, "p1")
        assert timer.resume() is False  # running, nothing banked
        timer.cancel()
        assert timer.resume() is False  # cancel clears the banked remainder

    asyncio.run(scenario())


# ── the rule itself ─────────────────────────────────────────────────────────


def _ctx() -> HookContext:
    return HookContext(event=GameEvent.ON_PLAY, actor_id="p1", card_id="src")


def test_set_rule_turn_timer_path() -> None:
    state = GameState(room_code="T", players=[Player(id="p1", name="A")])
    out = apply_op(state, SetRuleOp(path="turn_timer", value=45), _ctx())
    assert out.rules.turn_timer == 45
    lifted = apply_op(out, SetRuleOp(path="turn_timer", value=None), _ctx())
    assert lifted.rules.turn_timer is None


def test_set_rule_turn_timer_rejects_invalid() -> None:
    state = GameState(room_code="T", players=[Player(id="p1", name="A")])
    with pytest.raises(ValueError):
        apply_op(state, SetRuleOp(path="turn_timer", value=0), _ctx())


def test_room_creation_sets_rule_and_snapshot_carries_it() -> None:
    room = Room("CREATE", turn_timer=45)
    assert room.state.rules.turn_timer == 45
    snap = room.snapshot()
    assert snap["rules"]["turn_timer"] == 45
    assert snap["turn_timer"] is None  # no live clock before a turn starts

    manager = RoomManager()
    code = manager.create_room(turn_timer=20)
    assert manager.get(code).state.rules.turn_timer == 20
    code = manager.create_room()
    assert manager.get(code).state.rules.turn_timer is None


# ── arming at turn start + snapshot ─────────────────────────────────────────


def test_turn_start_arms_clock_and_snapshot_reports_it() -> None:
    room = _room(turn_timer=30)

    async def scenario() -> None:
        await room._start_turn("p1")
        timer = room._turn_timer
        assert timer.running and timer.player_id == "p1"
        snap = room.snapshot()
        assert snap["turn_timer"] == {
            "deadline_epoch_ms": timer.deadline_epoch_ms,
            "paused": False,
            "player_id": "p1",
        }
        timer.pause()
        assert room.snapshot()["turn_timer"] == {
            "deadline_epoch_ms": None,
            "paused": True,
            "player_id": "p1",
        }
        timer.cancel()

    asyncio.run(scenario())


def test_no_rule_means_no_clock() -> None:
    room = _room(turn_timer=None)

    async def scenario() -> None:
        socks = _connect(room, "p1", "p2")
        await room._start_turn("p1")
        assert not room._turn_timer.running and not room._turn_timer.paused
        assert room.snapshot()["turn_timer"] is None
        # Rooms that never had a clock stay silent — no turn_timer pushes.
        assert _timer_msgs(socks["p1"]) == []

    asyncio.run(scenario())


# ── expiry forces the end-turn path ─────────────────────────────────────────


def test_expiry_forces_end_turn_and_arms_next_player() -> None:
    room = _room(turn_timer=30)

    async def scenario() -> None:
        room._turn_timer.start(0.02, "p1")
        await asyncio.sleep(0.2)
        assert room.state.active_player().id == "p2"
        assert any("ran out of time" in line for line in room.state.log)
        # The next turn armed a fresh full clock for p2 from the rule.
        assert room._turn_timer.running and room._turn_timer.player_id == "p2"
        room._turn_timer.cancel()

    asyncio.run(scenario())


def test_expiry_noop_when_no_longer_that_players_turn() -> None:
    room = _room(turn_timer=30)

    async def scenario() -> None:
        room._turn_timer.start(0.02, "p2")  # armed for the NON-active player
        await asyncio.sleep(0.2)
        assert room.state.active_player().id == "p1"  # turn was not touched
        assert not any("ran out of time" in line for line in room.state.log)

    asyncio.run(scenario())


def test_expiry_noop_while_suspended() -> None:
    room = _room(turn_timer=30)

    async def scenario() -> None:
        room._resolving_play = "brewing-card"
        room._turn_timer.start(0.02, "p1")
        await asyncio.sleep(0.2)
        assert room.state.active_player().id == "p1"
        assert not any("ran out of time" in line for line in room.state.log)
        room._resolving_play = None

    asyncio.run(scenario())


def test_stale_expiry_after_pause_is_ignored() -> None:
    room = _room(turn_timer=30)

    async def scenario() -> None:
        room._turn_timer.start(0.02, "p1")
        room._turn_timer.pause()  # bumps the generation and banks the rest
        await asyncio.sleep(0.2)
        assert room.state.active_player().id == "p1"
        assert room._turn_timer.paused
        room._turn_timer.cancel()

    asyncio.run(scenario())


# ── pause during brewing (a play being interpreted) ─────────────────────────


def test_play_pauses_clock_while_brewing(monkeypatch) -> None:
    room = _room(turn_timer=30, p1_hand=["c1"])
    paused_during_resolve: list[bool] = []

    async def fake_resolve(self, card_id, card, actor_id=None, *, correlation_id):
        paused_during_resolve.append(self._turn_timer.paused)
        await asyncio.sleep(0.05)
        return ResolutionPlan(steps=[OpsStep(ops=[AddPointsOp(target="self", amount=1)])])

    monkeypatch.setattr(Room, "_resolve_plan", fake_resolve)

    async def scenario() -> None:
        socks = _connect(room, "p1", "p2")
        await room._start_turn("p1")
        await room.handle_action("p1", PlayMsg(card_id="c1"))
        assert paused_during_resolve == [True]
        # The play ended the turn: a fresh full clock is armed for p2.
        assert room._turn_timer.running and room._turn_timer.player_id == "p2"
        stamps = [(m["paused"], m["player_id"]) for m in _timer_msgs(socks["p2"])]
        assert stamps == [(False, "p1"), (True, "p1"), (False, "p2")]
        room._turn_timer.cancel()

    asyncio.run(scenario())


def test_clock_resumes_with_banked_remainder_when_turn_continues(monkeypatch) -> None:
    room = _room(turn_timer=30, p1_hand=["c1", "c2"])
    room.state = room.state.model_copy(update={"rules": room.state.rules.model_copy(update={"play": 2})})

    async def fake_resolve(self, card_id, card, actor_id=None, *, correlation_id):
        await asyncio.sleep(0.05)
        return ResolutionPlan(steps=[OpsStep(ops=[AddPointsOp(target="self", amount=1)])])

    monkeypatch.setattr(Room, "_resolve_plan", fake_resolve)

    async def scenario() -> None:
        await room._start_turn("p1")
        deadline_before = room._turn_timer.deadline_epoch_ms
        await room.handle_action("p1", PlayMsg(card_id="c1"))
        # One play of two: still p1's turn, clock resumed with the remainder
        # (the deadline moved later by the paused span, never earlier).
        assert room.state.active_player().id == "p1"
        assert room._turn_timer.running and room._turn_timer.player_id == "p1"
        assert room._turn_timer.deadline_epoch_ms >= deadline_before
        assert room._turn_timer.deadline_epoch_ms - time.time() * 1000 <= 30_000
        room._turn_timer.cancel()

    asyncio.run(scenario())


def test_lifting_the_rule_mid_pause_cancels_instead_of_resuming(monkeypatch) -> None:
    room = _room(turn_timer=30, p1_hand=["c1", "c2"])
    room.state = room.state.model_copy(update={"rules": room.state.rules.model_copy(update={"play": 2})})

    async def fake_resolve(self, card_id, card, actor_id=None, *, correlation_id):
        return ResolutionPlan(steps=[OpsStep(ops=[SetRuleOp(path="turn_timer", value=None)])])

    monkeypatch.setattr(Room, "_resolve_plan", fake_resolve)

    async def scenario() -> None:
        await room._start_turn("p1")
        await room.handle_action("p1", PlayMsg(card_id="c1"))
        assert room.state.active_player().id == "p1"
        assert not room._turn_timer.running and not room._turn_timer.paused

    asyncio.run(scenario())


# ── pause during a reaction window ──────────────────────────────────────────


def _reaction_setup() -> Room:
    room = _room(turn_timer=30)
    zap = {
        "id": "atk",
        "title": "Zap",
        "description": "Gain 5.",
        "canonical": {
            "target": "self",
            "placement": "discard",
            "venue": "all",
            "ops": [{"op": "add_points", "args": {"target": "self", "amount": 5}}],
        },
    }
    counter = {
        "id": "cs",
        "title": "Nuh-Uh",
        "description": "Counter.",
        "canonical": {
            "target": "self",
            "placement": "discard",
            "venue": "all",
            "trigger": "on_reaction",
            "ops": [{"op": "counter_play", "args": {"mode": "negate"}}],
        },
    }
    players = [
        room.state.players[0].model_copy(update={"hand": ["atk"]}),
        room.state.players[1].model_copy(update={"hand": ["cs"]}),
    ]
    room.state = room.state.model_copy(update={"cards": {"atk": zap, "cs": counter}, "players": players})
    return room


def test_reaction_window_pauses_clock_until_commit() -> None:
    room = _reaction_setup()

    async def scenario() -> None:
        socks = _connect(room, "p1", "p2")
        await room._start_turn("p1")
        await room.handle_action("p1", PlayMsg(card_id="atk"))
        assert room._pending is not None
        assert room._turn_timer.paused
        await room.handle_action("p2", PassReactionMsg(window_id=room.snapshot()["pending_play"]["window_id"]))
        # All eligible reactors passed: the play committed and the turn moved on.
        assert room._pending is None
        assert room.state.active_player().id == "p2"
        assert room._turn_timer.running and room._turn_timer.player_id == "p2"
        stamps = [(m["paused"], m["player_id"]) for m in _timer_msgs(socks["p1"])]
        assert stamps == [(False, "p1"), (True, "p1"), (False, "p2")]
        room._turn_timer.cancel()

    asyncio.run(scenario())


# ── pause during an interaction barrier ─────────────────────────────────────


def test_interaction_pauses_clock_until_resolution() -> None:
    room = _room(turn_timer=30, hand_limit=3, p1_hand=["a1", "a2", "a3", "a4"])

    async def scenario() -> None:
        _connect(room, "p1", "p2")
        room._turn_timer.start(30, "p1")
        await room.handle_action("p1", EndTurnMsg())
        pending = room._pending_resolution
        assert pending is not None and pending.purpose == "hand_limit"
        assert room._turn_timer.paused
        response = InteractionResponseMsg.model_validate(
            {"interaction_id": pending.interaction_id, "payload": {"kind": "card_pick", "card_id": "a2"}}
        )
        await room.handle_action("p1", response)
        assert room._pending_resolution is None
        assert room.state.active_player().id == "p2"
        assert room._turn_timer.running and room._turn_timer.player_id == "p2"
        room._turn_timer.cancel()

    asyncio.run(scenario())


# ── game end clears the clock ───────────────────────────────────────────────


def test_end_game_clears_clock() -> None:
    room = _room(turn_timer=30)

    async def scenario() -> None:
        socks = _connect(room, "p1", "p2")
        await room._start_turn("p1")
        assert room._turn_timer.running
        await room._end_game()
        assert not room._turn_timer.running and not room._turn_timer.paused
        last = _timer_msgs(socks["p1"])[-1]
        assert last == {"type": "turn_timer", "deadline_epoch_ms": None, "paused": False, "player_id": None}

    asyncio.run(scenario())
