"""Bead m23 (D1) — the agent's funny comment PERSISTS to the game log.

The interpretation agent produces a short in-character ``comment`` for every
free-text card it interprets. D1 makes that comment survive a reconnect/refresh:
transient WS messages (``brewing``/``card_interpreted``) are NOT re-sent to a
(re)joining client — it only gets the state snapshot — so the comment must be
appended to ``state.log`` (via ``_log_and_broadcast``), not merely broadcast.

These tests assert:
- The prefixed comment lands in ``room.state.log`` (persistence).
- It appears in ``room.snapshot()["log"]`` — what a refreshing client receives.
- Persistence is on GLOBAL state (every client's snapshot has it), not sent only
  to the actor.
- The deterministic compiled path produces NO arbiter line (agent not called).
- A target-requiring card logs its comment EXACTLY once across the
  resolve → prompt_choice → re-resolve round-trip.
- An empty comment adds no blank log line.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

from agent.contract import InterpretResult
from models.effects import AddPointsOp, EffectProgram
from models.ws_messages import PlayMsg
from board.rooms.room import AGENT_COMMENT_PREFIX, Room

COMMENT = "Wow, gain 5 points, how original."


def _playing_room(card: dict, *, hand_owner: str = "p1") -> Room:
    """Two-player playing room with ``card`` in ``hand_owner``'s hand.

    A non-empty deck plus ``_has_drawn`` keeps the play path clear of the
    draw-first gate and the end-of-game latch.
    """
    r = Room("ABCDEF")
    r.add_player("p1", "Alice")
    r.add_player("p2", "Bob")
    new_players = [
        p.model_copy(update={"hand": [card["id"]]}) if p.id == hand_owner else p.model_copy(update={"hand": ["other"]})
        for p in r.state.players
    ]
    r.state = r.state.model_copy(
        update={"phase": "playing", "deck": ["d1", "d2"], "cards": {card["id"]: card}, "players": new_players}
    )
    r._has_drawn = True
    return r


def _free_text_card(cid: str = "c1") -> dict:
    return {"id": cid, "title": "Mystery", "description": "Something happens.", "creator_id": "p1"}


def _ok_result(comment: str = COMMENT) -> InterpretResult:
    return InterpretResult(
        program=EffectProgram(ops=[AddPointsOp(target="self", amount=5)]),
        verdict="ok",
        comment=comment,
    )


def test_comment_persists_to_state_log() -> None:
    room = _playing_room(_free_text_card())
    room.connections.connect("p1", AsyncMock())
    room.connections.connect("p2", AsyncMock())

    with patch("agent.runtime.run_agent", return_value=_ok_result()):
        asyncio.run(room.handle_action("p1", PlayMsg(card_id="c1")))

    expected = f"{AGENT_COMMENT_PREFIX}{COMMENT}"
    assert expected in room.state.log


def test_comment_survives_reconnect_via_snapshot() -> None:
    # A refreshing/observing client only receives the state snapshot — the comment
    # must be in snapshot["log"] to survive the reload.
    room = _playing_room(_free_text_card())
    room.connections.connect("p1", AsyncMock())
    room.connections.connect("p2", AsyncMock())

    with patch("agent.runtime.run_agent", return_value=_ok_result()):
        asyncio.run(room.handle_action("p1", PlayMsg(card_id="c1")))

    snap = room.snapshot()
    assert any(COMMENT in line for line in snap["log"])


def test_comment_is_global_state_not_actor_only() -> None:
    # The log is global GameState, so a non-active observer's snapshot (built from
    # the same state) contains the comment. It is broadcast, not whispered to p1.
    room = _playing_room(_free_text_card())
    room.connections.connect("p1", AsyncMock())
    room.connections.connect("p2", AsyncMock())

    with patch("agent.runtime.run_agent", return_value=_ok_result()):
        asyncio.run(room.handle_action("p1", PlayMsg(card_id="c1")))

    # Any client rebuilding from the room's snapshot (e.g. observer p2) sees it.
    observer_view = room.snapshot()
    assert any(COMMENT in line for line in observer_view["log"])


def test_deterministic_compiled_path_produces_no_arbiter_line() -> None:
    # A card with canonical/compiled ops resolves deterministically — the agent is
    # never called, so there is no comment and no arbiter line.
    card = {
        "id": "c2",
        "title": "Gain 3",
        "description": "Gain 3 points.",
        "creator_id": "p1",
        "ops": [{"op": "add_points", "args": {"target": "self", "amount": 3}}],
    }
    room = _playing_room(card)
    room.connections.connect("p1", AsyncMock())
    room.connections.connect("p2", AsyncMock())

    with patch("agent.runtime.run_agent", side_effect=AssertionError("agent must not be called")) as spy:
        asyncio.run(room.handle_action("p1", PlayMsg(card_id="c2")))

    spy.assert_not_called()
    assert room.state.get_player("p1").score == 3
    assert not any(line.startswith(AGENT_COMMENT_PREFIX) for line in room.state.log)


def test_prompt_choice_round_trip_logs_comment_once() -> None:
    # A target-requiring card is resolved twice (resolve → prompt_choice →
    # follow-up play re-resolves). The arbiter comment must log exactly once.
    card = _free_text_card()
    room = _playing_room(card)
    room.connections.connect("p1", AsyncMock())
    room.connections.connect("p2", AsyncMock())

    chooser = InterpretResult(
        program=EffectProgram(ops=[AddPointsOp(target="chooser", amount=5)], requires_choice=True),
        verdict="ok",
        comment=COMMENT,
    )
    with patch("agent.runtime.run_agent", return_value=chooser):
        # First play: interpret -> needs a target -> prompt_choice (held pending).
        asyncio.run(room.handle_action("p1", PlayMsg(card_id="c1")))
        assert room.state.turn_index == 0  # not advanced yet
        # Follow-up play carrying the choice re-resolves and applies.
        asyncio.run(room.handle_action("p1", PlayMsg(card_id="c1", chosen_player_id="p2")))

    expected = f"{AGENT_COMMENT_PREFIX}{COMMENT}"
    assert room.state.log.count(expected) == 1
    assert room.state.get_player("p2").score == 5


def test_empty_comment_adds_no_blank_log_line() -> None:
    room = _playing_room(_free_text_card())
    room.connections.connect("p1", AsyncMock())
    room.connections.connect("p2", AsyncMock())

    with patch("agent.runtime.run_agent", return_value=_ok_result(comment="")):
        asyncio.run(room.handle_action("p1", PlayMsg(card_id="c1")))

    assert not any(line.startswith(AGENT_COMMENT_PREFIX) for line in room.state.log)
    assert AGENT_COMMENT_PREFIX not in room.state.log
