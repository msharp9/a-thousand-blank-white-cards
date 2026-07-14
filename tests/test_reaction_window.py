"""Tests for the reaction/counterspell window (board.rooms.room).

Reaction cards (canonical trigger "on_reaction") are unplayable on their
owner's turn; when another player plays a card, holders get a timed window to
react. All reaction cards here are ops-based (counter_play compiles
deterministically), so no LLM patching is needed.

Multi-step scenarios run inside one asyncio.run(...) so the window's timer
task lives on a single event loop.
"""

from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock

import board.rooms.room as room_module
from models.ws_messages import EndTurnMsg, PassReactionMsg, PlayMsg
from board.rooms.room import Room


def _card(cid: str, title: str, ops: list[dict], *, trigger: str | None = None, **extra) -> dict:
    return {
        "id": cid,
        "title": title,
        "description": f"{title}.",
        "canonical": {
            "target": "self",
            "placement": "discard",
            "venue": "all",
            "trigger": trigger,
            "ops": ops,
        },
        **extra,
    }


def _gain5(cid: str = "atk") -> dict:
    return _card(cid, "Zap", [{"op": "add_points", "args": {"target": "self", "amount": 5}}])


def _counterspell(cid: str = "cs", mode: str = "negate", side_ops: list[dict] | None = None) -> dict:
    ops = [{"op": "counter_play", "args": {"mode": mode}}, *(side_ops or [])]
    return _card(cid, "Nuh-Uh", ops, trigger="on_reaction")


def _reaction_room(*, p2_hand: list[dict] | None = None, p3_hand: list[dict] | None = None) -> Room:
    """Three-player room mid-game: p1 active holding Zap; p2/p3 hands as given."""
    room = Room("ABCDEF")
    for pid, name in (("p1", "Alice"), ("p2", "Bob"), ("p3", "Cara")):
        room.add_player(pid, name)
    p2_cards = p2_hand if p2_hand is not None else [_counterspell()]
    p3_cards = p3_hand or []
    cards = {c["id"]: c for c in [_gain5(), *p2_cards, *p3_cards]}
    players = [
        room.state.players[0].model_copy(update={"hand": ["atk"]}),
        room.state.players[1].model_copy(update={"hand": [c["id"] for c in p2_cards]}),
        room.state.players[2].model_copy(update={"hand": [c["id"] for c in p3_cards]}),
    ]
    room.state = room.state.model_copy(
        update={"phase": "playing", "deck": ["d1", "d2"], "cards": cards, "players": players}
    )
    room._has_drawn = True
    return room


def _connect_all(room: Room) -> dict[str, AsyncMock]:
    socks = {}
    for pid in ("p1", "p2", "p3"):
        socks[pid] = AsyncMock()
        room.connections.connect(pid, socks[pid])
    return socks


def _sent(sock: AsyncMock) -> list[dict]:
    return [json.loads(c.args[0]) for c in sock.send_text.call_args_list]


def _sent_types(sock: AsyncMock) -> list[str]:
    return [m["type"] for m in _sent(sock)]


def _score(room: Room, pid: str) -> int:
    return room.state.get_player(pid).score


# ── playing a reaction on your own turn ─────────────────────────────────────


def test_reaction_card_rejected_on_own_turn() -> None:
    room = _reaction_room()
    room.state = room.state.model_copy(update={"turn_index": 1})  # p2 active
    socks = _connect_all(room)
    asyncio.run(room.handle_action("p2", PlayMsg(card_id="cs")))
    assert "error" in _sent_types(socks["p2"])
    assert "cs" in room.state.get_player("p2").hand
    assert room._plays_this_turn == 0
    assert room.state.turn_index == 1


def test_reactions_only_hand_can_pass() -> None:
    # A hand of nothing but reaction cards must not deadlock the pass gate.
    room = _reaction_room()
    room.state = room.state.model_copy(update={"turn_index": 1})
    assert room._can_pass("p2") is True


# ── window opening conditions ───────────────────────────────────────────────


def test_no_window_when_no_reaction_holders() -> None:
    room = _reaction_room(p2_hand=[])
    _connect_all(room)
    asyncio.run(room.handle_action("p1", PlayMsg(card_id="atk")))
    assert room._pending is None
    assert _score(room, "p1") == 5
    assert room.state.turn_index == 1


def test_no_window_when_holder_disconnected() -> None:
    room = _reaction_room()
    sock1 = AsyncMock()
    room.connections.connect("p1", sock1)  # p2 (the holder) never connects
    asyncio.run(room.handle_action("p1", PlayMsg(card_id="atk")))
    assert room._pending is None
    assert _score(room, "p1") == 5


def test_uncounterable_card_skips_window() -> None:
    room = _reaction_room()
    cards = dict(room.state.cards)
    cards["atk"] = {**cards["atk"], "properties": {"uncounterable": True}}
    room.state = room.state.model_copy(update={"cards": cards})
    _connect_all(room)
    asyncio.run(room.handle_action("p1", PlayMsg(card_id="atk")))
    assert room._pending is None
    assert _score(room, "p1") == 5


def test_window_opens_and_suspends_play() -> None:
    room = _reaction_room()
    socks = _connect_all(room)

    async def scenario() -> None:
        await room.handle_action("p1", PlayMsg(card_id="atk"))
        assert room._pending is not None
        # Nothing resolved yet: card in hand, no points, turn not advanced.
        assert "atk" in room.state.get_player("p1").hand
        assert _score(room, "p1") == 0
        assert room.state.turn_index == 0
        # Everyone saw the window and the snapshot carries pending_play.
        for sock in socks.values():
            assert "reaction_window" in _sent_types(sock)
        snap = room.snapshot()
        assert snap["pending_play"]["card_id"] == "atk"
        assert snap["pending_play"]["actor_id"] == "p1"
        room._pending.timer.cancel()  # don't leak the timer past the loop

    asyncio.run(scenario())


def test_normal_actions_blocked_while_window_open() -> None:
    room = _reaction_room()
    socks = _connect_all(room)

    async def scenario() -> None:
        await room.handle_action("p1", PlayMsg(card_id="atk"))
        await room.handle_action("p1", EndTurnMsg())
        assert "error" in _sent_types(socks["p1"])
        assert room.state.turn_index == 0
        room._pending.timer.cancel()

    asyncio.run(scenario())


# ── reaction outcomes ───────────────────────────────────────────────────────


def _open_then_react(room: Room, reactor: str, reaction_msg: PlayMsg) -> None:
    async def scenario() -> None:
        await room.handle_action("p1", PlayMsg(card_id="atk"))
        assert room._pending is not None
        await room.handle_action(reactor, reaction_msg)

    asyncio.run(scenario())


def test_counterspell_negates_pending_play() -> None:
    room = _reaction_room()
    socks = _connect_all(room)
    _open_then_react(room, "p2", PlayMsg(card_id="cs", as_reaction=True))
    assert room._pending is None
    assert _score(room, "p1") == 0  # Zap never resolved
    assert "atk" in room.state.discard
    assert "cs" in room.state.discard
    assert room.state.turn_index == 1  # countered play still consumed the turn
    results = [m for m in _sent(socks["p3"]) if m["type"] == "reaction_result"]
    assert results and results[-1]["outcome"] == "countered"
    assert results[-1]["reactor_id"] == "p2"


def test_counter_with_side_effects_applies_them() -> None:
    side = [{"op": "add_points", "args": {"target": "self", "amount": 2}}]
    room = _reaction_room(p2_hand=[_counterspell(side_ops=side)])
    _connect_all(room)
    _open_then_react(room, "p2", PlayMsg(card_id="cs", as_reaction=True))
    assert _score(room, "p1") == 0
    assert _score(room, "p2") == 2


def test_steal_hand_moves_pending_card_to_reactor() -> None:
    room = _reaction_room(p2_hand=[_counterspell(mode="steal_hand")])
    socks = _connect_all(room)
    _open_then_react(room, "p2", PlayMsg(card_id="cs", as_reaction=True))
    assert _score(room, "p1") == 0
    assert "atk" in room.state.get_player("p2").hand
    assert "atk" not in room.state.discard
    results = [m for m in _sent(socks["p1"]) if m["type"] == "reaction_result"]
    assert results and results[-1]["outcome"] == "stolen"


def test_redirect_resolves_for_reactor() -> None:
    room = _reaction_room(p2_hand=[_counterspell(mode="redirect")])
    _connect_all(room)
    _open_then_react(room, "p2", PlayMsg(card_id="cs", as_reaction=True))
    # Zap ("gain 5" targeting self) resolves as if p2 had played it.
    assert _score(room, "p1") == 0
    assert _score(room, "p2") == 5
    assert "atk" in room.state.discard


def test_damp_squib_reaction_lets_original_resolve() -> None:
    # A reaction that never calls counter_play: side effects apply, original resolves.
    squib = _card("cs", "Boo", [{"op": "add_points", "args": {"target": "self", "amount": 2}}], trigger="on_reaction")
    room = _reaction_room(p2_hand=[squib])
    socks = _connect_all(room)
    _open_then_react(room, "p2", PlayMsg(card_id="cs", as_reaction=True))
    assert _score(room, "p1") == 5
    assert _score(room, "p2") == 2
    results = [m for m in _sent(socks["p1"]) if m["type"] == "reaction_result"]
    assert results and results[-1]["outcome"] == "resolved"


# ── timeout & passing ───────────────────────────────────────────────────────


def test_timeout_resolves_original_play(monkeypatch) -> None:
    monkeypatch.setattr(room_module, "REACTION_WINDOW_SECONDS", 0.05)
    room = _reaction_room()
    _connect_all(room)

    async def scenario() -> None:
        await room.handle_action("p1", PlayMsg(card_id="atk"))
        assert room._pending is not None
        await asyncio.sleep(0.4)

    asyncio.run(scenario())
    assert room._pending is None
    assert _score(room, "p1") == 5
    assert room.state.turn_index == 1


def test_all_eligible_passing_closes_window_early() -> None:
    room = _reaction_room()
    _connect_all(room)

    async def scenario() -> None:
        await room.handle_action("p1", PlayMsg(card_id="atk"))
        window_id = room._pending.window_id
        await room.handle_action("p2", PassReactionMsg(window_id=window_id))

    asyncio.run(scenario())
    assert room._pending is None
    assert _score(room, "p1") == 5
    assert "cs" in room.state.get_player("p2").hand  # reaction unspent


def test_partial_pass_keeps_window_open() -> None:
    room = _reaction_room(p3_hand=[_counterspell("cs3")])

    async def scenario() -> None:
        _connect_all(room)
        await room.handle_action("p1", PlayMsg(card_id="atk"))
        await room.handle_action("p2", PassReactionMsg())
        assert room._pending is not None  # p3 hasn't answered
        await room.handle_action("p3", PassReactionMsg())
        assert room._pending is None

    asyncio.run(scenario())
    assert _score(room, "p1") == 5


# ── races ───────────────────────────────────────────────────────────────────


def test_stale_timer_after_counter_is_noop() -> None:
    room = _reaction_room()
    _connect_all(room)

    async def scenario() -> None:
        await room.handle_action("p1", PlayMsg(card_id="atk"))
        stale_window_id = room._pending.window_id
        await room.handle_action("p2", PlayMsg(card_id="cs", as_reaction=True))
        assert room._pending is None
        # A stale timer firing after resolution must not double-commit.
        await room._reaction_timeout(stale_window_id, 0)

    asyncio.run(scenario())
    assert _score(room, "p1") == 0
    assert room.state.turn_index == 1
    assert room.state.log.count("Alice's Zap was countered!") == 1


def test_late_reaction_after_timeout_errors(monkeypatch) -> None:
    monkeypatch.setattr(room_module, "REACTION_WINDOW_SECONDS", 0.05)
    room = _reaction_room()
    socks = _connect_all(room)

    async def scenario() -> None:
        await room.handle_action("p1", PlayMsg(card_id="atk"))
        await asyncio.sleep(0.4)  # window times out, original resolves
        await room.handle_action("p2", PlayMsg(card_id="cs", as_reaction=True))

    asyncio.run(scenario())
    assert _score(room, "p1") == 5
    assert "cs" in room.state.get_player("p2").hand
    errors = [m for m in _sent(socks["p2"]) if m["type"] == "error"]
    assert any("closed" in m["message"] for m in errors)


# ── eligibility & claiming ──────────────────────────────────────────────────


def test_actor_cannot_react_to_own_play() -> None:
    room = _reaction_room()
    socks = _connect_all(room)
    _open_then_react(room, "p1", PlayMsg(card_id="atk", as_reaction=True))
    assert room._pending is not None
    assert "error" in _sent_types(socks["p1"])
    room._pending.timer.cancel()


def test_ineligible_player_cannot_react() -> None:
    room = _reaction_room()
    socks = _connect_all(room)
    _open_then_react(room, "p3", PlayMsg(card_id="cs", as_reaction=True))
    assert room._pending is not None
    assert "error" in _sent_types(socks["p3"])
    room._pending.timer.cancel()


def test_second_reactor_blocked_while_claimed() -> None:
    room = _reaction_room(p3_hand=[_counterspell("cs3")])
    socks = _connect_all(room)

    async def scenario() -> None:
        await room.handle_action("p1", PlayMsg(card_id="atk"))
        room._pending.claimed_by = "p2"  # p2 is mid-prompt_choice
        await room.handle_action("p3", PlayMsg(card_id="cs3", as_reaction=True))
        assert room._pending is not None
        errors = [m for m in _sent(socks["p3"]) if m["type"] == "error"]
        assert any("already reacting" in m["message"] for m in errors)
        room._pending.timer.cancel()

    asyncio.run(scenario())


def test_blank_cannot_be_played_as_reaction() -> None:
    blank = {"id": "b1", "title": "", "description": "", "blank": True}
    room = _reaction_room(p2_hand=[_counterspell(), blank])
    socks = _connect_all(room)
    _open_then_react(room, "p2", PlayMsg(card_id="b1", as_reaction=True))
    assert room._pending is not None
    errors = [m for m in _sent(socks["p2"]) if m["type"] == "error"]
    assert any("Blank" in m["message"] for m in errors)
    room._pending.timer.cancel()


def test_reaction_needing_choice_prompts_then_applies() -> None:
    # A steal-spell variant: counter AND drain 3 from a chosen player.
    stealer = _card(
        "cs",
        "Grabby Hands",
        [
            {"op": "counter_play", "args": {"mode": "negate"}},
            {"op": "steal_points", "args": {"from_target": "chosen_player", "to_target": "self", "amount": 3}},
        ],
        trigger="on_reaction",
    )
    room = _reaction_room(p2_hand=[stealer])
    scores = {p.id: 10 for p in room.state.players}
    room.state = room.state.model_copy(
        update={"players": [p.model_copy(update={"score": scores[p.id]}) for p in room.state.players]}
    )
    socks = _connect_all(room)

    async def scenario() -> None:
        await room.handle_action("p1", PlayMsg(card_id="atk"))
        await room.handle_action("p2", PlayMsg(card_id="cs", as_reaction=True))
        # Prompted for a target; window still open and claimed for p2.
        assert "prompt_choice" in _sent_types(socks["p2"])
        assert room._pending is not None and room._pending.claimed_by == "p2"
        await room.handle_action("p2", PlayMsg(card_id="cs", as_reaction=True, chosen_player_id="p1"))

    asyncio.run(scenario())
    assert room._pending is None
    assert _score(room, "p1") == 7  # drained 3, Zap countered
    assert _score(room, "p2") == 13


def test_no_nested_window_for_reactions() -> None:
    # p3 also holds a reaction, but a resolving reaction never opens a window.
    room = _reaction_room(p3_hand=[_counterspell("cs3")])
    socks = _connect_all(room)
    _open_then_react(room, "p2", PlayMsg(card_id="cs", as_reaction=True))
    assert room._pending is None
    windows = [m for m in _sent(socks["p3"]) if m["type"] == "reaction_window"]
    assert len(windows) == 1  # only the original play's window


def test_reaction_fires_on_reaction_hooks() -> None:
    # _emit_hooks(ON_REACTION) is a no-op without subscribers; here we just
    # assert the reaction path logs the reactor line players follow.
    room = _reaction_room()
    _connect_all(room)
    _open_then_react(room, "p2", PlayMsg(card_id="cs", as_reaction=True))
    assert any("Bob reacts with Nuh-Uh" in line for line in room.state.log)


# ── play-freeze coverage on the reaction path (bead phy.13) ─────────────────


def test_freeze_engaged_while_reaction_resolves_and_clears_after() -> None:
    # A reaction resolves via the same interpretation path as a direct play, so
    # the room must be frozen for its duration (else the active player's queued
    # turn actions execute against post-resolution state). We park the reactor
    # inside _execute_reaction and, from there, fire an EndTurn from the active
    # player: it must be rejected up front while _resolving_play is set.
    room = _reaction_room()
    socks = _connect_all(room)
    seen: dict[str, object] = {}

    async def scenario() -> None:
        await room.handle_action("p1", PlayMsg(card_id="atk"))
        assert room._pending is not None

        real_execute = room._execute_reaction

        async def parked_execute(*args, **kwargs):
            seen["resolving_during"] = room._resolving_play
            # A non-reaction action from the active player mid-resolution is
            # rejected by the pre-lock freeze, not queued behind the lock.
            await room.handle_action("p1", EndTurnMsg())
            return await real_execute(*args, **kwargs)

        room._execute_reaction = parked_execute
        await room.handle_action("p2", PlayMsg(card_id="cs", as_reaction=True))

    asyncio.run(scenario())

    assert seen["resolving_during"] == "cs"
    assert room._resolving_play is None  # cleared via finally
    p1_errors = [m for m in _sent(socks["p1"]) if m["type"] == "error"]
    assert any("finish resolving" in m["message"] for m in p1_errors)
    # The counter still resolved normally and consumed the turn.
    assert room._pending is None
    assert _score(room, "p1") == 0
    assert room.state.turn_index == 1


def test_reaction_message_is_exempt_from_the_freeze() -> None:
    # The freeze must not block reaction messages themselves — the reactor's own
    # as_reaction play carries the flag set, and pass_reaction is exempt too.
    room = _reaction_room(p3_hand=[_counterspell("cs3")])
    socks = _connect_all(room)

    async def scenario() -> None:
        await room.handle_action("p1", PlayMsg(card_id="atk"))
        # Simulate a resolution already in flight: the freeze flag is set, yet a
        # reaction / pass_reaction must still be accepted (not bounced).
        room._resolving_play = "atk"
        await room.handle_action("p3", PassReactionMsg())
        room._resolving_play = None
        await room.handle_action("p2", PlayMsg(card_id="cs", as_reaction=True))

    asyncio.run(scenario())

    p3_errors = [m["message"] for m in _sent(socks["p3"]) if m["type"] == "error"]
    assert not any("finish resolving" in m for m in p3_errors)
    assert room._pending is None
    assert _score(room, "p1") == 0  # countered
