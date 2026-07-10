"""Tests for the Room class (turn enforcement + state mutation)."""

from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, patch

from tbwc.models.effects import AddPointsOp, DestroyCardOp, EffectProgram
from tbwc.models.ws_messages import CreateCardMsg, PassMsg, PlayMsg, Placement, StartMsg
from tbwc.rooms.room import Room


def _room_with_two_players() -> Room:
    room = Room("ABCDEF")
    room.add_player("p1", "Alice")
    room.add_player("p2", "Bob")
    return room


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


def test_manual_draw_action_is_rejected() -> None:
    # The draw→play→pass model removed the manual draw action; a raw {"type":
    # "draw"} message is an unknown type and must not draw or advance.
    from types import SimpleNamespace

    room = _room_with_two_players()
    room.state = room.state.model_copy(update={"deck": ["c1", "c2"], "phase": "playing"})
    ws1 = AsyncMock()
    room.connections.connect("p1", ws1)
    asyncio.run(room.handle_action("p1", SimpleNamespace(type="draw")))
    sent_types = [json.loads(c.args[0])["type"] for c in ws1.send_text.call_args_list]
    assert "error" in sent_types
    assert room.state.deck == ["c1", "c2"]
    assert room.state.get_player("p1").hand == []


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


def test_start_sets_phase_playing() -> None:
    room = _room_with_two_players()
    room.connections.connect("p1", AsyncMock())

    import tbwc.rag.store as store

    store._client = None  # force the offline seed-file fallback
    asyncio.run(room.handle_action("p1", StartMsg()))
    assert room.state.phase == "playing"


def test_start_builds_deck_of_at_least_30_and_deals_hands() -> None:
    room = _room_with_two_players()
    room.connections.connect("p1", AsyncMock())
    room.connections.connect("p2", AsyncMock())

    # Force the offline path: no RAG store initialised -> seed-file fallback.
    import tbwc.rag.store as store

    store._client = None
    asyncio.run(room.handle_action("p1", StartMsg()))

    assert room.state.phase == "playing"
    assert len(room.state.deck) >= 30
    # Starting hands were dealt from the top of the deck. The first player (p1)
    # then auto-drew their turn-start card (draw_count=1), so p1 has 6 while the
    # off-turn p2 still has the dealt 5.
    assert len(room.state.get_player("p1").hand) == 6
    assert len(room.state.get_player("p2").hand) == 5
    # Every dealt/deck card id resolves in the registry.
    for p in room.state.players:
        assert all(cid in room.state.cards for cid in p.hand)
    assert all(cid in room.state.cards for cid in room.state.deck)


def test_start_auto_draws_for_first_player() -> None:
    # At game start the first player's turn begins immediately, auto-drawing
    # draw_count cards on top of the dealt starting hand.
    room = _room_with_two_players()
    room.connections.connect("p1", AsyncMock())
    room.connections.connect("p2", AsyncMock())

    import tbwc.rag.store as store

    store._client = None
    asyncio.run(room.handle_action("p1", StartMsg()))

    # p1 (active) got STARTING_HAND_SIZE + draw_count; p2 only the dealt hand.
    assert len(room.state.get_player("p1").hand) == 5 + room.state.draw_count
    assert len(room.state.get_player("p2").hand) == 5
    assert room.state.turn_index == 0


def test_create_card_off_turn_allowed() -> None:
    room = _room_with_two_players()
    room.state = room.state.model_copy(update={"phase": "playing"})
    room.connections.connect("p2", AsyncMock())
    fake_result = {"program": None, "snippet": None, "verdict": "invalid"}
    with patch("tbwc.agent.graph.interpret_card", return_value=fake_result):
        asyncio.run(room.handle_action("p2", CreateCardMsg(title="Wild", description="do something")))
    assert len(room.state.cards) == 1


def _chooser_room() -> Room:
    """Two-player room mid-game with a single 'chooser'-target card in p1's hand."""
    room = _room_with_two_players()
    room.state = room.state.model_copy(
        update={
            "phase": "playing",
            "deck": ["d1", "d2"],  # non-empty so the post-play auto-draw doesn't end the game
            "cards": {"c1": {"id": "c1", "title": "Bless", "description": "give a chosen player points"}},
        }
    )
    return room


def _chooser_result() -> dict:
    """Interpretation result with a chooser-target op requiring a choice."""
    program = EffectProgram(ops=[AddPointsOp(target="chooser", amount=5)], requires_choice=True)
    return {"program": program, "snippet": None, "verdict": "ok"}


def test_play_chooser_card_with_valid_choice_applies() -> None:
    room = _chooser_room()
    room.connections.connect("p1", AsyncMock())
    room.connections.connect("p2", AsyncMock())
    msg = PlayMsg(card_id="c1", placement=Placement(zone="player", target_player_id="p2"), chosen_player_id="p2")
    with patch("tbwc.agent.graph.interpret_card", return_value=_chooser_result()):
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
    with patch("tbwc.agent.graph.interpret_card", return_value=_chooser_result()):
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
    with patch("tbwc.agent.graph.interpret_card", return_value=_chooser_result()):
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
    with patch("tbwc.agent.graph.interpret_card", return_value=_chooser_result()):
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
            "deck": ["d1", "d2"],  # non-empty so the post-play auto-draw doesn't end the game
            "cards": {"c1": {"id": "c1", "title": "Zap", "description": "destroy a chosen card"}},
        }
    )
    new_players = [p.model_copy(update={"in_play": ["t1"]}) if p.id == "p1" else p for p in room.state.players]
    room.state = room.state.model_copy(update={"players": new_players})
    return room


def _card_chooser_result() -> dict:
    program = EffectProgram(ops=[DestroyCardOp(card_target="chosen_card")], requires_choice=True)
    return {"program": program, "snippet": None, "verdict": "ok"}


def test_play_card_choice_with_valid_card_applies() -> None:
    room = _card_chooser_room()
    room.connections.connect("p1", AsyncMock())
    room.connections.connect("p2", AsyncMock())
    msg = PlayMsg(card_id="c1", placement=Placement(zone="center"), chosen_card_id="t1")
    with patch("tbwc.agent.graph.interpret_card", return_value=_card_chooser_result()):
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
    with patch("tbwc.agent.graph.interpret_card", return_value=_card_chooser_result()):
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
    with patch("tbwc.agent.graph.interpret_card", return_value=_card_chooser_result()):
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
    with patch("tbwc.agent.graph.interpret_card", return_value=_card_chooser_result()):
        asyncio.run(room.handle_action("p1", msg))
    sent_types = [json.loads(c.args[0])["type"] for c in ws1.send_text.call_args_list]
    assert "error" in sent_types
    assert "t1" in room.state.get_player("p1").in_play
    assert room.state.turn_index == 0


# ── draw → play → pass turn model ──
def _playing_room(deck: list[str]) -> Room:
    room = _room_with_two_players()
    room.state = room.state.model_copy(update={"phase": "playing", "deck": list(deck)})
    return room


def test_pass_advances_turn_and_next_player_auto_draws() -> None:
    room = _playing_room(["d1", "d2", "d3"])
    ws1, ws2 = AsyncMock(), AsyncMock()
    room.connections.connect("p1", ws1)
    room.connections.connect("p2", ws2)
    asyncio.run(room.handle_action("p1", PassMsg()))
    # Turn advanced to p2, and p2 auto-drew draw_count cards on turn start.
    assert room.state.turn_index == 1
    assert room.state.get_player("p2").hand == ["d1"]
    assert room.state.deck == ["d2", "d3"]
    # p1 (who passed) never drew.
    assert room.state.get_player("p1").hand == []
    ws2.send_text.assert_called()


def test_normal_draw_count_does_not_trigger_second_draw() -> None:
    # With draw_count=1 the hand is non-empty after the turn-start draw, so the
    # pragmatic "draw a second card if you have no playable card" rule does NOT
    # fire — exactly draw_count cards are drawn.
    room = _playing_room(["d1", "d2", "d3", "d4"])
    room.connections.connect("p1", AsyncMock())
    room.connections.connect("p2", AsyncMock())
    asyncio.run(room.handle_action("p1", PassMsg()))
    assert room.state.get_player("p2").hand == ["d1"]  # only draw_count=1 card


def test_second_draw_fires_when_hand_still_empty_after_draw() -> None:
    # Pragmatic second-draw rule: if the hand is STILL empty after the turn-start
    # draw (e.g. draw_count=0, our "no playable card" proxy), draw one more so the
    # player is never stranded with an empty hand while cards remain.
    room = _playing_room(["d1", "d2"])
    room.state = room.state.model_copy(update={"draw_count": 0})
    room.connections.connect("p1", AsyncMock())
    room.connections.connect("p2", AsyncMock())
    asyncio.run(room.handle_action("p1", PassMsg()))
    # p2 drew 0 (draw_count) then 1 more because the hand was still empty.
    assert room.state.get_player("p2").hand == ["d1"]


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
    ws1, ws2 = AsyncMock(), AsyncMock()
    room.connections.connect("p1", ws1)
    room.connections.connect("p2", ws2)
    asyncio.run(room.handle_action("p1", PassMsg()))
    # Deck empty when p2's turn begins -> game ends, p2 (highest score) wins.
    assert room.state.phase == "ended"
    assert room.state.winner_ids == ["p2"]
    # Both players received the final state broadcast (nobody stuck).
    ws1.send_text.assert_called()
    ws2.send_text.assert_called()
    assert any("Winner" in line for line in room.state.log)


def test_last_card_drawer_finishes_turn_before_end() -> None:
    # A turn that begins with exactly one card left: the player draws it (deck
    # now empty) and the game does NOT end yet — they still get to act. Ending
    # is deferred to the next turn start on the empty deck.
    room = _playing_room(["last"])
    room.connections.connect("p1", AsyncMock())
    room.connections.connect("p2", AsyncMock())
    asyncio.run(room._start_turn("p1"))
    assert room.state.phase == "playing"
    assert room.state.deck == []
    assert room.state.get_player("p1").hand == ["last"]


def test_deck_exhaustion_end_to_end_via_pass() -> None:
    # p1 has the last card available; p1 passes -> turn moves to p2, whose
    # turn-start finds an empty deck and ends the game. p1's own turn completed
    # first (it did not error).
    room = _playing_room(["last"])
    ws1, ws2 = AsyncMock(), AsyncMock()
    room.connections.connect("p1", ws1)
    room.connections.connect("p2", ws2)
    # Simulate p1's turn already underway (has drawn 'last').
    room.state = room.state.model_copy(
        update={
            "deck": [],
            "players": [
                room.state.players[0].model_copy(update={"hand": ["last"], "score": 5}),
                room.state.players[1],
            ],
        }
    )
    asyncio.run(room.handle_action("p1", PassMsg()))
    assert room.state.phase == "ended"
    assert room.state.winner_ids == ["p1"]  # p1 has 5, p2 has 0
