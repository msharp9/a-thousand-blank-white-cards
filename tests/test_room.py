"""Tests for the Room class (turn enforcement + state mutation)."""

from __future__ import annotations

import asyncio
import json
import random
from unittest.mock import AsyncMock, patch

from conftest import drive_to_playing, ready_card_result

from agent.contract import InterpretResult
from models.effects import AddPointsOp, DestroyCardOp, EffectProgram
from models.ws_messages import CreateCardMsg, PassMsg, PlayMsg, Placement, StartMsg
from board.rooms.room import CARDS_TO_AUTHOR, Room


def _room_with_two_players() -> Room:
    room = Room("ABCDEF")
    room.add_player("p1", "Alice")
    room.add_player("p2", "Bob")
    return room


def _plain_cards() -> list[dict]:
    """A fixed, ops-free premade-pool source for hand-size assertions.

    The real seed corpus includes play_on_draw cards (e.g. Landmine) that
    auto-play the instant they land in a hand, which would make exact
    hand-size assertions flaky against an unseeded deal from the full corpus.
    """
    return [{"id": f"plain-{i}", "title": f"T{i}", "description": f"D{i}"} for i in range(40)]


def test_room_constructs() -> None:
    room = Room("ABCDEF")
    assert room.code == "ABCDEF"
    assert room.state.room_code == "ABCDEF"
    assert room.get_player_ids() == []


def test_add_player_is_immutable_reassign() -> None:
    room = _room_with_two_players()
    assert room.get_player_ids() == ["p1", "p2"]


def test_pass_off_turn_sends_error() -> None:
    room = _room_with_two_players()
    room.state = room.state.model_copy(update={"deck": ["c1", "c2"], "phase": "playing"})
    ws2 = AsyncMock()
    room.connections.connect("p2", ws2)  # p2 is NOT active (turn_index 0 -> p1)
    asyncio.run(room.handle_action("p2", PassMsg()))
    # p2 got an error, turn/deck unchanged
    sent_types = [json.loads(c.args[0])["type"] for c in ws2.send_text.call_args_list]
    assert "error" in sent_types
    assert room.state.deck == ["c1", "c2"]
    assert room.state.turn_index == 0


def test_turn_start_auto_draws_for_active_player() -> None:
    # The auto-draw→play→end model: starting a turn draws draw_count card(s)
    # off the top of the deck for the active player, no client message needed.
    room = _room_with_two_players()
    room.state = room.state.model_copy(update={"deck": ["c1", "c2"], "phase": "playing"})
    room.connections.connect("p1", AsyncMock())
    asyncio.run(room._start_turn("p1"))
    assert room.state.get_player("p1").hand == ["c1"]  # draw_count == 1
    assert room.state.deck == ["c2"]
    assert room._has_drawn is True


def test_auto_draw_broadcasts_state_with_updated_hand() -> None:
    # The turn-start auto-draw must push a fresh 'state' snapshot so the client
    # sees the new hand and has_drawn=true immediately, without a reconnect.
    room = _room_with_two_players()
    room.state = room.state.model_copy(update={"deck": ["c1", "c2"], "phase": "playing"})
    ws1 = AsyncMock()
    room.connections.connect("p1", ws1)
    asyncio.run(room._start_turn("p1"))
    states = [json.loads(c.args[0]) for c in ws1.send_text.call_args_list]
    state_msgs = [m for m in states if m["type"] == "state"]
    assert state_msgs, "auto-draw did not broadcast a state snapshot"
    latest = state_msgs[-1]["state"]
    assert latest["has_drawn"] is True
    p1 = next(p for p in latest["players"] if p["id"] == "p1")
    assert p1["hand"] == ["c1"]


def test_turn_start_on_empty_deck_latches_has_drawn() -> None:
    # An empty deck satisfies the draw step without touching anything: the
    # snapshot still reports has_drawn=True so clients unlock Play/Pass.
    room = _room_with_two_players()
    room.state = room.state.model_copy(update={"deck": [], "phase": "playing"})
    ws1 = AsyncMock()
    room.connections.connect("p1", ws1)
    asyncio.run(room._start_turn("p1"))
    assert room._has_drawn is True
    assert room.state.get_player("p1").hand == []
    msgs = [json.loads(c.args[0]) for c in ws1.send_text.call_args_list]
    state_msgs = [m for m in msgs if m["type"] == "state"]
    assert state_msgs, "empty-deck turn start did not broadcast a state snapshot"
    assert state_msgs[-1]["state"]["has_drawn"] is True


def test_effect_log_persists_in_state_for_refresh() -> None:
    # Every effect_applied broadcast must ALSO be appended to state.log so a
    # client that refreshes/reconnects can rehydrate its log from the snapshot
    # (the frontend seeds its log from msg.state.log). A pass is the simplest
    # log-producing action.
    room = _room_with_two_players()
    room.state = room.state.model_copy(update={"deck": ["c1", "c2"], "phase": "playing"})
    room.connections.connect("p1", AsyncMock())
    room.connections.connect("p2", AsyncMock())

    assert room.state.log == []
    asyncio.run(room.handle_action("p1", PassMsg()))
    # The pass line is captured in the persistent snapshot log, and the snapshot
    # exposes it so a rejoining client rebuilds history.
    assert any("passed" in line for line in room.state.log)
    assert room.snapshot()["log"] == room.state.log


def test_start_sets_phase_setup() -> None:
    # A single StartMsg from the lobby now lands in the SETUP phase (card
    # authoring), not straight into "playing".
    room = _room_with_two_players()
    room.connections.connect("p1", AsyncMock())

    import agent.rag.store as store

    store._client = None  # force the offline seed-file fallback
    asyncio.run(room.handle_action("p1", StartMsg()))
    assert room.state.phase == "setup"


def test_start_sets_phase_playing() -> None:
    # Driving the full two-step flow (start -> author 5 each -> start) reaches
    # phase="playing".
    room = _room_with_two_players()
    room.connections.connect("p1", AsyncMock())
    room.connections.connect("p2", AsyncMock())

    import agent.rag.store as store

    store._client = None  # force the offline seed-file fallback
    drive_to_playing(room, ["p1", "p2"])
    assert room.state.phase == "playing"


def test_start_builds_deck_of_at_least_30_and_deals_hands() -> None:
    room = _room_with_two_players()
    room.connections.connect("p1", AsyncMock())
    room.connections.connect("p2", AsyncMock())

    # Force the offline path: no RAG store initialised -> seed-file fallback.
    import agent.rag.store as store

    store._client = None
    with patch("board.rooms.deck._default_card_source", _plain_cards):
        drive_to_playing(room, ["p1", "p2"])

    assert room.state.phase == "playing"
    # Deck was finalised: 30 premade + 10 authored + 10 blanks = 50, minus the
    # 10 cards dealt (5 each) and the first player's turn-start auto-draw = 39.
    total_hands = sum(len(p.hand) for p in room.state.players)
    assert len(room.state.deck) + total_hands == 50
    assert len(room.state.deck) == 39
    # Starting hands were dealt from the top of the deck; whichever player the
    # shuffled turn_order put first then began with the automatic draw of
    # their draw_count card(s) — turn order is randomized, not host-first.
    first_id = room.state.active_player().id
    other_id = "p2" if first_id == "p1" else "p1"
    assert len(room.state.get_player(first_id).hand) == 5 + room.state.draw_count
    assert len(room.state.get_player(other_id).hand) == 5
    # Every dealt/deck card id resolves in the registry.
    for p in room.state.players:
        assert all(cid in room.state.cards for cid in p.hand)
    assert all(cid in room.state.cards for cid in room.state.deck)


def test_first_player_auto_drawn_at_game_start() -> None:
    # The setup→playing transition starts the shuffled turn_order's first
    # player's turn, which auto-draws for them: they begin holding
    # STARTING_HAND_SIZE + draw_count cards with has_drawn already latched;
    # the other player holds only the deal.
    room = _room_with_two_players()
    room.connections.connect("p1", AsyncMock())
    room.connections.connect("p2", AsyncMock())

    import agent.rag.store as store

    store._client = None

    with patch("board.rooms.deck._default_card_source", _plain_cards):
        drive_to_playing(room, ["p1", "p2"])

    first_id = room.state.turn_order[0]
    other_id = "p2" if first_id == "p1" else "p1"
    assert room.state.active_player().id == first_id
    assert len(room.state.get_player(first_id).hand) == 5 + room.state.draw_count
    assert len(room.state.get_player(other_id).hand) == 5
    assert room._has_drawn is True


def _room_three_players() -> Room:
    room = Room("ABCDEF")
    room.add_player("p1", "Alice")
    room.add_player("p2", "Bob")
    room.add_player("p3", "Cy")
    room.connections.connect("p1", AsyncMock())
    room.connections.connect("p2", AsyncMock())
    room.connections.connect("p3", AsyncMock())
    return room


def test_turn_order_is_a_permutation_of_player_ids() -> None:
    room = _room_three_players()
    import agent.rag.store as store

    store._client = None
    drive_to_playing(room, ["p1", "p2", "p3"])
    assert sorted(room.state.turn_order) == ["p1", "p2", "p3"]


def test_turn_order_shuffle_is_seedable_and_need_not_start_the_host() -> None:
    # Seating order is p1, p2, p3 (p1 is the host/players[0]); a seeded shuffle
    # can — and here does — put someone else first, proving the game no longer
    # always opens on the host.
    room = _room_three_players()

    async def scenario() -> None:
        await room.handle_action("p1", StartMsg())
        for pid in ("p1", "p2"):
            for i in range(CARDS_TO_AUTHOR):
                await room.handle_action(pid, CreateCardMsg(title=f"{pid}-{i}", description="gain 1 point"))
        for i in range(CARDS_TO_AUTHOR - 1):
            await room.handle_action("p3", CreateCardMsg(title=f"p3-{i}", description="gain 1 point"))
        await room.wait_for_card_drafts()

        canonical = room._canonicalize_interpretation(ready_card_result())
        room.state = room.state.model_copy(
            update={
                "cards": {
                    **room.state.cards,
                    "p3-last": {
                        "id": "p3-last",
                        "title": "p3-last",
                        "description": "gain 1 point",
                        "creator_id": "p3",
                        "origin": "authored",
                        "draft_status": "ready",
                        "draft_revision": 1,
                        **canonical,
                    },
                }
            }
        )
        await room._start_playing(rng=random.Random(1))

    with patch("agent.runtime.run_agent", return_value=ready_card_result()):
        asyncio.run(scenario())

    assert room.state.phase == "playing"
    # Contract, not the exact Random(1) byte sequence: the order is a permutation
    # of the player ids, and for this seed it is genuinely reshuffled — it does
    # not simply reproduce seating order and does not open on the host.
    assert sorted(room.state.turn_order) == ["p1", "p2", "p3"]
    assert room.state.turn_order != ["p1", "p2", "p3"]  # not seating order
    assert room.state.turn_order[0] != "p1"  # does not open on the host
    assert room.state.active_player().id == room.state.turn_order[0]


def test_create_card_rejected_outside_setup() -> None:
    # Authoring is setup-only: playing/results creates get the standard error
    # envelope and register nothing (mid-game authoring is playing a blank).
    for phase in ("playing", "results"):
        room = _room_with_two_players()
        room.state = room.state.model_copy(update={"phase": phase})
        ws2 = AsyncMock()
        room.connections.connect("p2", ws2)
        asyncio.run(room.handle_action("p2", CreateCardMsg(title="Wild", description="do something")))
        assert room.state.cards == {}
        sent = [json.loads(c.args[0]) for c in ws2.send_text.call_args_list]
        assert any(m["type"] == "error" for m in sent)


def _chooser_room() -> Room:
    """Two-player room mid-game with a single 'chooser'-target card in p1's hand."""
    room = _room_with_two_players()
    room.state = room.state.model_copy(
        update={
            "phase": "playing",
            # Non-empty deck so drawing the last card doesn't latch end-of-game;
            # _has_drawn mirrors the turn-start auto-draw bookkeeping.
            "deck": ["d1", "d2"],
            "cards": {"c1": {"id": "c1", "title": "Bless", "description": "give a chosen player points"}},
        }
    )
    room._has_drawn = True
    return room


def _chooser_result() -> InterpretResult:
    """Interpretation result with a chooser-target op requiring a choice."""
    program = EffectProgram(ops=[AddPointsOp(target="chooser", amount=5)], requires_choice=True)
    return InterpretResult(program=program, snippet=None, verdict="ok")


def test_play_chooser_card_with_valid_choice_applies() -> None:
    room = _chooser_room()
    room.connections.connect("p1", AsyncMock())
    room.connections.connect("p2", AsyncMock())
    msg = PlayMsg(card_id="c1", placement=Placement(zone="player", target_player_id="p2"), chosen_player_id="p2")
    with patch("agent.runtime.run_agent", return_value=_chooser_result()):
        asyncio.run(room.handle_action("p1", msg))
    # The chosen player received the points and the turn advanced.
    assert room.state.get_player("p2").score == 5
    assert room.state.turn_index == 1


def test_play_chooser_card_without_choice_prompts() -> None:
    # Under the card-driven targeting design, a play that needs a player choice
    # but supplies none is HELD PENDING: the server sends a prompt_choice with
    # the candidate players and does NOT advance the turn (no error, no score
    # change, card not consumed).
    room = _chooser_room()
    ws1 = AsyncMock()
    room.connections.connect("p1", ws1)
    room.connections.connect("p2", AsyncMock())
    msg = PlayMsg(card_id="c1", chosen_player_id=None)
    with patch("agent.runtime.run_agent", return_value=_chooser_result()):
        asyncio.run(room.handle_action("p1", msg))
    sent = [json.loads(c.args[0]) for c in ws1.send_text.call_args_list]
    sent_types = [m["type"] for m in sent]
    assert "prompt_choice" in sent_types
    assert "error" not in sent_types
    prompt = next(m for m in sent if m["type"] == "prompt_choice")
    assert prompt["card_id"] == "c1"
    assert {c["player_id"] for c in prompt["choices"]} == {"p1", "p2"}
    assert all("name" in c for c in prompt["choices"])
    # Turn NOT advanced, no score applied.
    assert room.state.get_player("p2").score == 0
    assert room.state.turn_index == 0


def test_play_chooser_followup_with_choice_applies_and_advances() -> None:
    # The follow-up play carrying the picked chosen_player_id applies the effect
    # and advances the turn (the second play re-interprets — no server pending
    # state is held).
    room = _chooser_room()
    room.connections.connect("p1", AsyncMock())
    room.connections.connect("p2", AsyncMock())
    with patch("agent.runtime.run_agent", return_value=_chooser_result()):
        asyncio.run(room.handle_action("p1", PlayMsg(card_id="c1")))  # prompt
        asyncio.run(room.handle_action("p1", PlayMsg(card_id="c1", chosen_player_id="p2")))  # answer
    assert room.state.get_player("p2").score == 5
    assert room.state.turn_index == 1


def test_play_chooser_card_with_invalid_choice_errors_cleanly() -> None:
    room = _chooser_room()
    ws1 = AsyncMock()
    room.connections.connect("p1", ws1)
    room.connections.connect("p2", AsyncMock())
    msg = PlayMsg(card_id="c1", placement=Placement(zone="center"), chosen_player_id="ghost")
    with patch("agent.runtime.run_agent", return_value=_chooser_result()):
        asyncio.run(room.handle_action("p1", msg))
    sent_types = [json.loads(c.args[0])["type"] for c in ws1.send_text.call_args_list]
    assert "error" in sent_types
    assert room.state.get_player("p2").score == 0
    assert room.state.turn_index == 0


# ── chosen_card (CardTarget axis) plumbing ──
def _card_chooser_room() -> Room:
    """Two-player room mid-game; p1 has a 'destroy a chosen card' card 'c1',
    and target card 't1' sits in p1's in-play zone."""
    room = _room_with_two_players()
    room.state = room.state.model_copy(
        update={
            "phase": "playing",
            # Non-empty deck so drawing the last card doesn't latch end-of-game;
            # _has_drawn is set below so these tests exercise the play path.
            "deck": ["d1", "d2"],
            "cards": {"c1": {"id": "c1", "title": "Zap", "description": "destroy a chosen card"}},
        }
    )
    new_players = [p.model_copy(update={"in_play": ["t1"]}) if p.id == "p1" else p for p in room.state.players]
    room.state = room.state.model_copy(update={"players": new_players})
    room._has_drawn = True
    return room


def _card_chooser_result() -> InterpretResult:
    program = EffectProgram(ops=[DestroyCardOp(card_target="chosen_card")], requires_choice=True)
    return InterpretResult(program=program, snippet=None, verdict="ok")


def test_play_card_choice_with_valid_card_applies() -> None:
    room = _card_chooser_room()
    room.connections.connect("p1", AsyncMock())
    room.connections.connect("p2", AsyncMock())
    msg = PlayMsg(card_id="c1", placement=Placement(zone="center"), chosen_card_id="t1")
    with patch("agent.runtime.run_agent", return_value=_card_chooser_result()):
        asyncio.run(room.handle_action("p1", msg))
    # The chosen card was destroyed and the turn advanced.
    assert "t1" not in room.state.get_player("p1").in_play
    assert "t1" in room.state.discard
    assert room.state.turn_index == 1


def test_play_card_choice_without_card_prompts() -> None:
    # The card-target axis also prompts (rather than errors) when no card was
    # chosen: the server sends a prompt_choice listing the selectable cards and
    # holds the play pending (no destruction, turn NOT advanced).
    room = _card_chooser_room()
    ws1 = AsyncMock()
    room.connections.connect("p1", ws1)
    room.connections.connect("p2", AsyncMock())
    msg = PlayMsg(card_id="c1", chosen_card_id=None)
    with patch("agent.runtime.run_agent", return_value=_card_chooser_result()):
        asyncio.run(room.handle_action("p1", msg))
    sent = [json.loads(c.args[0]) for c in ws1.send_text.call_args_list]
    sent_types = [m["type"] for m in sent]
    assert "prompt_choice" in sent_types
    assert "error" not in sent_types
    prompt = next(m for m in sent if m["type"] == "prompt_choice")
    assert "t1" in {c["card_id"] for c in prompt["choices"]}
    assert "t1" in room.state.get_player("p1").in_play
    assert room.state.turn_index == 0


def test_play_card_choice_followup_with_card_applies_and_advances() -> None:
    room = _card_chooser_room()
    room.connections.connect("p1", AsyncMock())
    room.connections.connect("p2", AsyncMock())
    with patch("agent.runtime.run_agent", return_value=_card_chooser_result()):
        asyncio.run(room.handle_action("p1", PlayMsg(card_id="c1")))  # prompt
        asyncio.run(room.handle_action("p1", PlayMsg(card_id="c1", chosen_card_id="t1")))  # answer
    assert "t1" not in room.state.get_player("p1").in_play
    assert "t1" in room.state.discard
    assert room.state.turn_index == 1


def test_play_card_choice_with_invalid_card_errors_cleanly() -> None:
    room = _card_chooser_room()
    ws1 = AsyncMock()
    room.connections.connect("p1", ws1)
    room.connections.connect("p2", AsyncMock())
    msg = PlayMsg(card_id="c1", placement=Placement(zone="center"), chosen_card_id="ghost_card")
    with patch("agent.runtime.run_agent", return_value=_card_chooser_result()):
        asyncio.run(room.handle_action("p1", msg))
    sent_types = [json.loads(c.args[0])["type"] for c in ws1.send_text.call_args_list]
    assert "error" in sent_types
    assert "t1" in room.state.get_player("p1").in_play
    assert room.state.turn_index == 0


def test_legacy_card_choice_prompt_offers_hand_and_in_play_but_not_the_played_card() -> None:
    # Unscoped chosen_card keeps the legacy universe (public in-play + the
    # actor's own hand) minus the card being played itself.
    room = _card_chooser_room()
    players = [p.model_copy(update={"hand": ["c1", "h1"]}) if p.id == "p1" else p for p in room.state.players]
    room.state = room.state.model_copy(
        update={
            "players": players,
            "cards": {**room.state.cards, "h1": {"id": "h1", "title": "Handy"}, "t1": {"id": "t1", "title": "Target"}},
        }
    )
    ws1 = AsyncMock()
    room.connections.connect("p1", ws1)
    room.connections.connect("p2", AsyncMock())
    with patch("agent.runtime.run_agent", return_value=_card_chooser_result()):
        asyncio.run(room.handle_action("p1", PlayMsg(card_id="c1")))
    sent = [json.loads(c.args[0]) for c in ws1.send_text.call_args_list]
    prompt = next(m for m in sent if m["type"] == "prompt_choice")
    assert [c["card_id"] for c in prompt["choices"]] == ["t1", "h1"]  # in-play first, no c1


# ── chosen_card scoped to a declared zone (move_cards from_zone) ──
def _center_exile_room(*, center: list[str] = ["hr1", "hr2"]) -> Room:
    """p1 holds 'void' (exile a chosen CENTER card) plus a hand card; the
    shared center holds `center`; p1 also has an in-play card."""
    room = _room_with_two_players()
    ops = [{"op": "move_cards", "args": {"card_target": "chosen_card", "from_zone": "center", "to_zone": "exile"}}]
    cards = {
        "void": {
            "id": "void",
            "title": "Into the Void",
            "description": "exile a center card",
            "canonical": {"ops": ops},
        },
        "h1": {"id": "h1", "title": "Handy"},
        "ip1": {"id": "ip1", "title": "Board"},
        "hr1": {"id": "hr1", "title": "Rule One"},
        "hr2": {"id": "hr2", "title": "Rule Two"},
    }
    players = [
        p.model_copy(update={"hand": ["void", "h1"], "in_play": ["ip1"]}) if p.id == "p1" else p
        for p in room.state.players
    ]
    room.state = room.state.model_copy(
        update={
            "phase": "playing",
            "deck": ["d1", "d2"],
            "cards": cards,
            "players": players,
            "house_rules": list(center),
        }
    )
    room._has_drawn = True
    room.connections.connect("p1", AsyncMock())
    room.connections.connect("p2", AsyncMock())
    return room


def test_scoped_card_choice_prompt_offers_every_center_card_and_nothing_else() -> None:
    room = _center_exile_room()
    asyncio.run(room.handle_action("p1", PlayMsg(card_id="void")))
    ws1 = room.connections._connections["p1"]
    sent = [json.loads(c.args[0]) for c in ws1.send_text.call_args_list]
    prompt = next(m for m in sent if m["type"] == "prompt_choice")
    assert [c["card_id"] for c in prompt["choices"]] == ["hr1", "hr2"]
    assert room.state.turn_index == 0  # held pending


def test_scoped_card_choice_moves_exactly_the_chosen_card_to_exile() -> None:
    room = _center_exile_room()
    asyncio.run(room.handle_action("p1", PlayMsg(card_id="void", chosen_card_id="hr1")))
    assert room.state.exiled == ["hr1"]
    assert room.state.house_rules == ["hr2"]
    assert "void" in room.state.discard
    assert room.state.turn_index == 1


def test_scoped_card_choice_rejects_a_forged_hand_card_without_consuming_the_play() -> None:
    room = _center_exile_room()
    asyncio.run(room.handle_action("p1", PlayMsg(card_id="void", chosen_card_id="h1")))
    ws1 = room.connections._connections["p1"]
    sent = [json.loads(c.args[0]) for c in ws1.send_text.call_args_list]
    assert any(m["type"] == "error" and "Invalid target card" in m["message"] for m in sent)
    assert room.state.exiled == []
    assert "h1" in room.state.get_player("p1").hand
    assert "void" in room.state.get_player("p1").hand  # play not consumed
    assert room.state.turn_index == 0
    assert room._plays_this_turn == 0


def test_scoped_card_choice_with_empty_center_errors_and_never_falls_back_to_a_hand() -> None:
    room = _center_exile_room(center=[])
    asyncio.run(room.handle_action("p1", PlayMsg(card_id="void")))
    ws1 = room.connections._connections["p1"]
    sent = [json.loads(c.args[0]) for c in ws1.send_text.call_args_list]
    assert not any(m["type"] == "prompt_choice" for m in sent)
    assert any(m["type"] == "error" and "no eligible target card" in m["message"] for m in sent)
    assert "void" in room.state.get_player("p1").hand
    assert room.state.turn_index == 0


def test_scoped_exile_retires_the_center_cards_hooks() -> None:
    from models.game_state import HookSpec

    room = _center_exile_room(center=["hr1"])
    room.state = room.state.model_copy(
        update={
            "hooks": [
                HookSpec(
                    id="hook-hr1",
                    source_card_id="hr1",
                    event="on_turn_start",
                    scope="center",
                    code="def apply(state, ctx):\n    state.add_points('self', 1)\n",
                )
            ]
        }
    )
    asyncio.run(room.handle_action("p1", PlayMsg(card_id="void", chosen_card_id="hr1")))
    assert room.state.exiled == ["hr1"]
    assert room.state.hooks == []


# ── shared candidate policy (board.rooms.choices) ──
def _plan(*ops):
    from models.effects import OpsStep, ResolutionPlan

    return ResolutionPlan(steps=[OpsStep(ops=list(ops))])


def _choice_state():
    from models.game_state import GameState, Player

    return GameState(
        room_code="CHOICE",
        players=[
            Player(id="p1", name="Alice", hand=["src", "h1"], in_play=["ipA"]),
            Player(id="p2", name="Bob", hand=["h2"], in_play=["ipB"]),
        ],
        house_rules=["hr1", "hr2"],
        phase="playing",
    )


def test_candidates_intersect_across_multiple_chosen_card_ops() -> None:
    from board.rooms.choices import chosen_card_candidates
    from models.effects import MoveCardsOp

    plan = _plan(
        DestroyCardOp(card_target="chosen_card"),  # legacy: in-play + actor hand
        MoveCardsOp(card_target="chosen_card", from_zone="in_play", from_player="all", to_zone="discard"),
    )
    # Intersection keeps only in-play cards, in the FIRST set's order.
    assert chosen_card_candidates(_choice_state(), plan, "p1", "src") == ["ipA", "ipB"]


def test_candidates_scoped_to_a_chosen_players_hand() -> None:
    from board.rooms.choices import chosen_card_candidates
    from models.effects import MoveCardsOp

    plan = _plan(MoveCardsOp(card_target="chosen_card", from_zone="hand", from_player="chooser", to_zone="discard"))
    state = _choice_state()
    assert chosen_card_candidates(state, plan, "p1", "src", chosen_player_id="p2") == ["h2"]
    # Unresolvable owner (no player picked yet) offers nothing — never a fallback.
    assert chosen_card_candidates(state, plan, "p1", "src") == []


def test_candidates_never_leak_the_hidden_deck() -> None:
    from board.rooms.choices import chosen_card_candidates
    from models.effects import MoveCardsOp

    plan = _plan(MoveCardsOp(card_target="chosen_card", from_zone="deck", to_zone="discard"))
    state = _choice_state().model_copy(update={"deck": ["d1", "d2", "d3"]})
    # deck is hidden — a chosen_card scoped to it offers nothing, never the deck itself.
    assert chosen_card_candidates(state, plan, "p1", "src") == []


def test_candidates_scoped_to_the_public_discard() -> None:
    from board.rooms.choices import chosen_card_candidates
    from models.effects import MoveCardsOp

    plan = _plan(MoveCardsOp(card_target="chosen_card", from_zone="discard", to_zone="exile"))
    state = _choice_state().model_copy(update={"discard": ["dc1", "dc2"]})
    assert chosen_card_candidates(state, plan, "p1", "src") == ["dc1", "dc2"]


def test_plan_choice_needs_detects_the_from_player_axis() -> None:
    from board.rooms.choices import plan_choice_needs
    from models.effects import MoveCardsOp

    plan = _plan(MoveCardsOp(card_target="chosen_card", from_zone="hand", from_player="chooser", to_zone="discard"))
    assert plan_choice_needs(plan) == (True, True)


# ── two-step victim-hand selection (Grand Theft shape, bead 0fr.2) ──
GRAND_THEFT_OPS = [
    {
        "op": "move_cards",
        "args": {
            "card_target": "chosen_card",
            "from_zone": "hand",
            "from_player": "chooser",
            "to_zone": "hand",
            "to_player": "self",
        },
    },
    {"op": "subtract_points", "args": {"target": "chooser", "amount": 3}},
]


def _theft_room() -> Room:
    """p1 (Alice, active) holds 'theft' + own card; Bob and Cara hold hidden hands."""
    room = Room("ABCDEF")
    room.add_player("p1", "Alice")
    room.add_player("p2", "Bob")
    room.add_player("p3", "Cara")
    hands = {"p1": ["theft", "alice-own"], "p2": ["bob-secret-1", "bob-secret-2"], "p3": ["cara-secret-1"]}
    cards = {
        "theft": {
            "id": "theft",
            "title": "Grand Theft",
            "description": "Choose a player, take one of their cards, and cost them 3 points.",
            "canonical": {"ops": GRAND_THEFT_OPS},
        },
    }
    for hand in hands.values():
        for cid in hand:
            cards.setdefault(cid, {"id": cid, "title": cid.upper()})
    players = [p.model_copy(update={"hand": hands[p.id], "score": 5}) for p in room.state.players]
    room.state = room.state.model_copy(
        update={"phase": "playing", "deck": ["d1", "d2"], "cards": cards, "players": players}
    )
    room._has_drawn = True
    for pid in ("p1", "p2", "p3"):
        room.connections.connect(pid, AsyncMock())
    return room


def _msgs(room: Room, pid: str) -> list[dict]:
    ws = room.connections._connections[pid]
    return [json.loads(c.args[0]) for c in ws.send_text.call_args_list]


def test_theft_prompts_player_first_including_self() -> None:
    room = _theft_room()
    asyncio.run(room.handle_action("p1", PlayMsg(card_id="theft")))
    prompt = next(m for m in _msgs(room, "p1") if m["type"] == "prompt_choice")
    assert {c["player_id"] for c in prompt["choices"]} == {"p1", "p2", "p3"}
    assert prompt["cards"] == {}
    assert prompt["as_reaction"] is False
    assert room.state.turn_index == 0


def test_theft_card_prompt_offers_only_the_chosen_victims_hand_with_snapshots() -> None:
    room = _theft_room()
    asyncio.run(room.handle_action("p1", PlayMsg(card_id="theft")))
    asyncio.run(room.handle_action("p1", PlayMsg(card_id="theft", chosen_player_id="p2")))
    prompts = [m for m in _msgs(room, "p1") if m["type"] == "prompt_choice"]
    card_prompt = prompts[-1]
    assert [c["card_id"] for c in card_prompt["choices"]] == ["bob-secret-1", "bob-secret-2"]
    # The prompt carries the accumulated context + full snapshots for exactly
    # the candidates (the chooser's redacted snapshot hides Bob's hand).
    assert card_prompt["chosen_player_id"] == "p2"
    assert set(card_prompt["cards"]) == {"bob-secret-1", "bob-secret-2"}
    assert card_prompt["cards"]["bob-secret-1"]["title"] == "BOB-SECRET-1"
    assert room.state.turn_index == 0  # still held pending


def test_theft_choosing_self_offers_own_other_cards_only() -> None:
    room = _theft_room()
    asyncio.run(room.handle_action("p1", PlayMsg(card_id="theft", chosen_player_id="p1")))
    prompt = next(m for m in _msgs(room, "p1") if m["type"] == "prompt_choice")
    assert [c["card_id"] for c in prompt["choices"]] == ["alice-own"]  # never Grand Theft itself


def test_theft_final_followup_transfers_exactly_the_chosen_card_and_penalizes_once() -> None:
    room = _theft_room()
    asyncio.run(room.handle_action("p1", PlayMsg(card_id="theft")))
    asyncio.run(room.handle_action("p1", PlayMsg(card_id="theft", chosen_player_id="p2")))
    asyncio.run(
        room.handle_action("p1", PlayMsg(card_id="theft", chosen_player_id="p2", chosen_card_id="bob-secret-2"))
    )
    assert "bob-secret-2" in room.state.get_player("p1").hand
    # Bob keeps his other card (plus his next turn's auto-draw) — exactly the
    # selected card moved.
    assert "bob-secret-2" not in room.state.get_player("p2").hand
    assert "bob-secret-1" in room.state.get_player("p2").hand
    assert room.state.get_player("p2").score == 2
    assert room.state.get_player("p1").score == 5
    assert "theft" in room.state.discard
    assert room.state.turn_index == 1  # resolved exactly once, turn advanced
    prompts = [m for m in _msgs(room, "p1") if m["type"] == "prompt_choice"]
    assert len(prompts) == 2  # player, then card — no re-prompt loop


def test_theft_rejects_a_forged_card_outside_the_chosen_hand() -> None:
    room = _theft_room()
    for forged in ("alice-own", "cara-secret-1"):
        asyncio.run(room.handle_action("p1", PlayMsg(card_id="theft", chosen_player_id="p2", chosen_card_id=forged)))
    errors = [m for m in _msgs(room, "p1") if m["type"] == "error"]
    assert len([m for m in errors if "Invalid target card" in m["message"]]) == 2
    assert "theft" in room.state.get_player("p1").hand  # play not consumed
    assert room.state.get_player("p2").hand == ["bob-secret-1", "bob-secret-2"]
    assert all(p.score == 5 for p in room.state.players)
    assert room.state.turn_index == 0
    assert room._plays_this_turn == 0


def test_theft_rejects_a_stale_card_that_left_the_victims_hand() -> None:
    room = _theft_room()
    asyncio.run(room.handle_action("p1", PlayMsg(card_id="theft", chosen_player_id="p2")))
    # Bob's hand changes between the prompt and the answer.
    players = [p.model_copy(update={"hand": ["bob-secret-1"]}) if p.id == "p2" else p for p in room.state.players]
    room.state = room.state.model_copy(update={"players": players, "discard": ["bob-secret-2"]})
    asyncio.run(
        room.handle_action("p1", PlayMsg(card_id="theft", chosen_player_id="p2", chosen_card_id="bob-secret-2"))
    )
    errors = [m for m in _msgs(room, "p1") if m["type"] == "error"]
    assert any("Invalid target card" in m["message"] for m in errors)
    assert "theft" in room.state.get_player("p1").hand
    assert all(p.score == 5 for p in room.state.players)
    assert room.state.turn_index == 0


def test_theft_leaks_no_victim_hand_data_to_other_players_or_spectators() -> None:
    room = _theft_room()
    room.add_spectator("spec", "Watcher")
    room.connections.connect("spec", AsyncMock())
    asyncio.run(room.handle_action("p1", PlayMsg(card_id="theft")))
    asyncio.run(room.handle_action("p1", PlayMsg(card_id="theft", chosen_player_id="p2")))
    # The card prompt (ids + snapshots of Bob's hidden hand) goes ONLY to Alice.
    for pid in ("p3", "spec"):
        raw = [c.args[0] for c in room.connections._connections[pid].send_text.call_args_list]
        assert all("bob-secret" not in payload for payload in raw)
    assert not any(m["type"] == "prompt_choice" for m in _msgs(room, "p2"))
    assert not any(m["type"] == "prompt_choice" for m in _msgs(room, "p3"))


# ── auto-draw → play → pass turn model ──
def _playing_room(deck: list[str]) -> Room:
    room = _room_with_two_players()
    room.state = room.state.model_copy(update={"phase": "playing", "deck": list(deck)})
    return room


def test_pass_advances_turn_and_next_player_is_auto_drawn() -> None:
    room = _playing_room(["d1", "d2", "d3"])
    ws1, ws2 = AsyncMock(), AsyncMock()
    room.connections.connect("p1", ws1)
    room.connections.connect("p2", ws2)
    # p1 ends their turn; advancing starts p2's turn, which auto-draws for them.
    asyncio.run(room.handle_action("p1", PassMsg()))
    assert room.state.turn_index == 1
    assert room.state.get_player("p2").hand == ["d1"]
    assert room.state.deck == ["d2", "d3"]
    assert room._has_drawn is True
    ws2.send_text.assert_called()


def test_game_start_sets_turn_number_to_one() -> None:
    room = _room_with_two_players()
    room.connections.connect("p1", AsyncMock())
    room.connections.connect("p2", AsyncMock())

    import agent.rag.store as store

    store._client = None
    drive_to_playing(room, ["p1", "p2"])
    assert room.state.turn_number == 1
    assert room.snapshot()["turn_number"] == 1


def test_pass_increments_turn_number_each_new_active_player() -> None:
    room = _playing_room(["d1", "d2", "d3"])
    room.state = room.state.model_copy(update={"turn_number": 1})
    room.connections.connect("p1", AsyncMock())
    room.connections.connect("p2", AsyncMock())
    asyncio.run(room.handle_action("p1", PassMsg()))
    assert room.state.turn_number == 2
    asyncio.run(room.handle_action("p2", PassMsg()))
    assert room.state.turn_number == 3


def test_extra_turn_does_not_increment_turn_number() -> None:
    # The current player's "go again" leaves turn_index AND turn_number alone
    # since the same turn continues rather than a new one starting.
    room = _playing_room(["d1", "d2", "d3"])
    room.state = room.state.model_copy(update={"turn_number": 1}).with_condition("p1", "extra_turn", True)
    room.connections.connect("p1", AsyncMock())
    room.connections.connect("p2", AsyncMock())
    asyncio.run(room.handle_action("p1", PassMsg()))
    assert room.state.turn_index == 0  # still p1
    assert room.state.turn_number == 1  # unchanged


def test_auto_draw_takes_exactly_draw_count_cards() -> None:
    # The turn-start auto-draw takes exactly draw_count (default 1) card(s) —
    # there is no pragmatic "extra card" rescue.
    room = _playing_room(["d1", "d2", "d3", "d4"])
    room.connections.connect("p1", AsyncMock())
    room.connections.connect("p2", AsyncMock())
    asyncio.run(room._start_turn("p1"))
    assert room.state.get_player("p1").hand == ["d1"]  # only draw_count=1 card
    assert room.state.deck == ["d2", "d3", "d4"]


def test_auto_draw_with_zero_draw_count_takes_nothing() -> None:
    # With draw_count=0 the turn-start auto-draw takes no cards: the draw step
    # is still marked done (has_drawn) so the player can play/pass.
    room = _playing_room(["d1", "d2"])
    room.state = room.state.model_copy(update={"rules": room.state.rules.model_copy(update={"draw": 0})})
    room.connections.connect("p1", AsyncMock())
    room.connections.connect("p2", AsyncMock())
    asyncio.run(room._start_turn("p1"))
    assert room.state.get_player("p1").hand == []
    assert room.state.deck == ["d1", "d2"]
    assert room._has_drawn is True


def test_hand_grows_by_draw_count_each_turn() -> None:
    # Hand-size accounting over several turns: every turn start auto-draws
    # exactly draw_count, so after two full rounds of passing each player holds
    # exactly the cards drawn for them.
    room = _playing_room(["d1", "d2", "d3", "d4", "d5", "d6"])
    room.connections.connect("p1", AsyncMock())
    room.connections.connect("p2", AsyncMock())
    asyncio.run(room._start_turn("p1"))  # turn 1: p1 auto-draws d1
    asyncio.run(room.handle_action("p1", PassMsg()))  # turn 2: p2 auto-draws d2
    asyncio.run(room.handle_action("p2", PassMsg()))  # turn 3: p1 auto-draws d3
    asyncio.run(room.handle_action("p1", PassMsg()))  # turn 4: p2 auto-draws d4
    assert room.state.get_player("p1").hand == ["d1", "d3"]
    assert room.state.get_player("p2").hand == ["d2", "d4"]
    assert room.state.deck == ["d5", "d6"]


def test_pass_on_empty_deck_ends_game_with_winner() -> None:
    room = _playing_room([])
    room.state = room.state.model_copy(
        update={
            "players": [
                room.state.players[0].model_copy(update={"score": 3}),
                room.state.players[1].model_copy(update={"score": 7}),
            ]
        }
    )
    # Model "the last card was already drawn earlier": exhaustion latched, deck
    # empty. p1's pass (allowed with no deck) ends their turn -> the game ends.
    room._deck_exhausted = True
    ws1, ws2 = AsyncMock(), AsyncMock()
    room.connections.connect("p1", ws1)
    room.connections.connect("p2", ws2)
    asyncio.run(room.handle_action("p1", PassMsg()))
    # Turn ends on an exhausted deck -> end-of-game resolves, winners are
    # computed, and the results screen shows (phase="results"). p2 (highest
    # score) is the winner.
    assert room.state.phase == "results"
    assert room.state.winner_ids == ["p2"]
    # Both players received the final state broadcast (nobody stuck).
    ws1.send_text.assert_called()
    ws2.send_text.assert_called()
    assert any("Winner" in line for line in room.state.log)


def test_last_card_drawer_finishes_turn_before_end() -> None:
    # A turn starting with exactly one card left: the auto-draw takes it (deck
    # now empty, exhaustion latched) and the game does NOT end yet — the drawer
    # still gets to act. Ending is deferred until this turn ends (_advance_turn).
    room = _playing_room(["last"])
    room.connections.connect("p1", AsyncMock())
    room.connections.connect("p2", AsyncMock())
    asyncio.run(room._start_turn("p1"))
    assert room.state.phase == "playing"
    assert room.state.deck == []
    assert room.state.get_player("p1").hand == ["last"]
    assert room._deck_exhausted is True


def test_deck_exhaustion_end_to_end_via_pass() -> None:
    # p1's turn-start auto-draw takes the last card (exhaustion latches), then
    # they pass -> the turn ends and the game ends. p1's own turn completed
    # first (the pass did not error).
    room = _playing_room(["last"])
    ws1, ws2 = AsyncMock(), AsyncMock()
    room.connections.connect("p1", ws1)
    room.connections.connect("p2", ws2)
    room.state = room.state.model_copy(
        update={
            "players": [
                room.state.players[0].model_copy(update={"score": 5}),
                room.state.players[1],
            ],
        }
    )
    asyncio.run(room._start_turn("p1"))  # auto-draws 'last', latches exhaustion
    assert room.state.get_player("p1").hand == ["last"]
    asyncio.run(room.handle_action("p1", PassMsg()))  # ends turn -> end-of-game
    # End-of-game shows the results screen with winners already computed.
    assert room.state.phase == "results"
    assert room.state.winner_ids == ["p1"]  # p1 has 5, p2 has 0


# ── blank cards: authored on play ──
def _blank_room() -> Room:
    """Two-player room mid-game with a single blank card 'blank-0' in the deck."""
    room = _room_with_two_players()
    room.state = room.state.model_copy(
        update={
            "phase": "playing",
            # Non-empty deck so drawing the last card doesn't latch end-of-game;
            # _has_drawn is set so these tests exercise the (author-on-)play path.
            "deck": ["d1", "d2"],
            "cards": {
                "blank-0": {"id": "blank-0", "title": "", "description": "", "blank": True, "creator_id": "blank"}
            },
        }
    )
    room._has_drawn = True
    return room


def _self_points_result() -> InterpretResult:
    """A simple 'give self points' interpretation (no target choice needed)."""
    return InterpretResult(
        program=EffectProgram(ops=[AddPointsOp(target="self", amount=3)]), snippet=None, verdict="ok"
    )


def test_play_blank_authors_card_and_applies_and_advances() -> None:
    # Playing a blank with title+description fills in the card (title/description
    # persisted, blank flag cleared, creator set to the player), interprets it,
    # applies the effect, and advances the turn.
    room = _blank_room()
    room.connections.connect("p1", AsyncMock())
    room.connections.connect("p2", AsyncMock())
    msg = PlayMsg(card_id="blank-0", title="Gain 3", description="Gain 3 points.")
    with patch("agent.runtime.run_agent", return_value=_self_points_result()) as mock_interp:
        asyncio.run(room.handle_action("p1", msg))
    # Card was authored in the registry.
    card = room.state.cards["blank-0"]
    assert card["title"] == "Gain 3"
    assert card["description"] == "Gain 3 points."
    assert card["creator_id"] == "p1"
    assert "blank" not in card
    # Interpreter saw the AUTHORED text (persist-before-interpret ordering) plus
    # the live state, actor_id and creator_id (the player who authored the blank).
    from models.game_state import GameState

    mock_interp.assert_called_once()
    call = mock_interp.call_args
    assert call.args[0] == "Gain 3"
    assert call.args[1] == "Gain 3 points."
    assert isinstance(call.args[2], GameState)  # live GameState passed to the agent
    assert call.args[3] == "p1"  # actor_id
    assert call.kwargs["creator_id"] == "p1"  # authored-on-play: creator == actor
    # Effect applied and turn advanced.
    assert room.state.get_player("p1").score == 3
    assert room.state.turn_index == 1


def test_play_blank_persists_authoring_before_interpretation_for_followup() -> None:
    # Core ordering: the authored content is persisted BEFORE interpretation, so
    # a prompt_choice follow-up play (which omits title/description) re-interprets
    # the now-real card. Simulate: first play is a blank chooser card (prompts),
    # second play carries only the chosen_player_id.
    room = _blank_room()
    room.connections.connect("p1", AsyncMock())
    room.connections.connect("p2", AsyncMock())
    with patch("agent.runtime.run_agent", return_value=_chooser_result()) as mock_interp:
        # First play: author the blank + interpret -> needs a target -> prompt.
        asyncio.run(room.handle_action("p1", PlayMsg(card_id="blank-0", title="Bless", description="give points")))
        # After the first play the blank is already a real, authored card.
        card = room.state.cards["blank-0"]
        assert card["title"] == "Bless"
        assert "blank" not in card
        assert room.state.turn_index == 0  # held pending, no advance yet
        # The interpretation's structured ops are persisted as the card's
        # canonical, so the follow-up (choice-only) play resolves
        # DETERMINISTICALLY — no second LLM round-trip.
        asyncio.run(room.handle_action("p1", PlayMsg(card_id="blank-0", chosen_player_id="p2")))
    assert mock_interp.call_args_list[0].args[:2] == ("Bless", "give points")
    assert len(mock_interp.call_args_list) == 1
    assert room.state.cards["blank-0"]["canonical"]["ops"]
    assert room.state.get_player("p2").score == 5
    assert room.state.turn_index == 1


def test_play_blank_without_content_is_rejected() -> None:
    # A blank played with no title/description is guarded: an error is sent, the
    # card stays blank, and the turn does not advance (nothing interpreted).
    room = _blank_room()
    ws1 = AsyncMock()
    room.connections.connect("p1", ws1)
    room.connections.connect("p2", AsyncMock())
    with patch("agent.runtime.run_agent", return_value=_self_points_result()) as mock_interp:
        asyncio.run(room.handle_action("p1", PlayMsg(card_id="blank-0")))
    sent_types = [json.loads(c.args[0])["type"] for c in ws1.send_text.call_args_list]
    assert "error" in sent_types
    mock_interp.assert_not_called()
    assert room.state.cards["blank-0"]["blank"] is True
    assert room.state.turn_index == 0


def test_play_allowance_of_two_keeps_turn_until_spent() -> None:
    c1 = {
        "id": "c1",
        "title": "One",
        "description": "x",
        "canonical": {"ops": [{"op": "add_points", "args": {"target": "self", "amount": 1}}]},
    }
    c2 = {
        "id": "c2",
        "title": "Two",
        "description": "x",
        "canonical": {"ops": [{"op": "add_points", "args": {"target": "self", "amount": 1}}]},
    }
    room = _playing_room(["d1", "d2", "d3"])
    room.state = room.state.model_copy(
        update={
            "cards": {**room.state.cards, "c1": c1, "c2": c2},
            "players": [
                room.state.players[0].model_copy(update={"hand": ["c1", "c2"]}),
                room.state.players[1],
            ],
            "rules": room.state.rules.model_copy(update={"play": 2}),
        }
    )
    room.connections.connect("p1", AsyncMock())
    room.connections.connect("p2", AsyncMock())
    room._has_drawn = True

    asyncio.run(room.handle_action("p1", PlayMsg(card_id="c1")))
    assert room.state.active_player().id == "p1"

    asyncio.run(room.handle_action("p1", PlayMsg(card_id="c2")))
    assert room.state.active_player().id == "p2"


def test_play_disabled_when_rule_is_zero() -> None:
    c1 = {"id": "c1", "title": "One", "description": "x"}
    room = _playing_room(["d1"])
    room.state = room.state.model_copy(
        update={
            "cards": {**room.state.cards, "c1": c1},
            "players": [
                room.state.players[0].model_copy(update={"hand": ["c1"]}),
                room.state.players[1],
            ],
            "rules": room.state.rules.model_copy(update={"play": 0}),
        }
    )
    ws = AsyncMock()
    room.connections.connect("p1", ws)
    room._has_drawn = True

    asyncio.run(room.handle_action("p1", PlayMsg(card_id="c1")))
    assert room.state.get_player("p1").hand == ["c1"]
    assert room.state.active_player().id == "p1"
