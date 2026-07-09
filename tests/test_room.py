"""Tests for the Room class (turn enforcement + state mutation)."""

from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, patch

from tbwc.models.effects import AddPointsOp, DestroyCardOp, EffectProgram
from tbwc.models.ws_messages import CreateCardMsg, DrawMsg, PlayMsg, Placement, StartMsg
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


def test_draw_off_turn_sends_error() -> None:
    room = _room_with_two_players()
    room.state = room.state.model_copy(update={"deck": ["c1", "c2"], "phase": "playing"})
    ws2 = AsyncMock()
    room.connections.connect("p2", ws2)  # p2 is NOT active (turn_index 0 -> p1)
    asyncio.run(room.handle_action("p2", DrawMsg()))
    # p2 got an error, deck unchanged
    ws2.send_text.assert_called_once()
    sent = json.loads(ws2.send_text.call_args.args[0])
    assert sent["type"] == "error"
    assert room.state.deck == ["c1", "c2"]


def test_draw_on_turn_draws_and_broadcasts() -> None:
    room = _room_with_two_players()
    room.state = room.state.model_copy(update={"deck": ["c1", "c2"], "phase": "playing"})
    ws1, ws2 = AsyncMock(), AsyncMock()
    room.connections.connect("p1", ws1)
    room.connections.connect("p2", ws2)
    asyncio.run(room.handle_action("p1", DrawMsg()))
    # p1 drew c1; deck now [c2]; both got a state broadcast
    assert room.state.deck == ["c2"]
    assert "c1" in room.state.get_player("p1").hand
    ws1.send_text.assert_called()
    ws2.send_text.assert_called()


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
    # Starting hands were dealt from the top of the deck.
    assert len(room.state.get_player("p1").hand) == 5
    assert len(room.state.get_player("p2").hand) == 5
    # Every dealt/deck card id resolves in the registry.
    for p in room.state.players:
        assert all(cid in room.state.cards for cid in p.hand)
    assert all(cid in room.state.cards for cid in room.state.deck)


def test_draw_works_after_start() -> None:
    room = _room_with_two_players()
    room.connections.connect("p1", AsyncMock())
    room.connections.connect("p2", AsyncMock())

    import tbwc.rag.store as store

    store._client = None
    asyncio.run(room.handle_action("p1", StartMsg()))

    deck_before = len(room.state.deck)
    hand_before = len(room.state.get_player("p1").hand)
    asyncio.run(room.handle_action("p1", DrawMsg()))  # p1 is the active player
    assert len(room.state.deck) == deck_before - 1
    assert len(room.state.get_player("p1").hand) == hand_before + 1


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


def test_play_chooser_card_without_choice_errors_cleanly() -> None:
    room = _chooser_room()
    ws1 = AsyncMock()
    room.connections.connect("p1", ws1)
    room.connections.connect("p2", AsyncMock())
    msg = PlayMsg(card_id="c1", placement=Placement(zone="center"), chosen_player_id=None)
    with patch("tbwc.agent.graph.interpret_card", return_value=_chooser_result()):
        asyncio.run(room.handle_action("p1", msg))
    # An error was sent to the active player, no score change, turn NOT advanced.
    sent_types = [json.loads(c.args[0])["type"] for c in ws1.send_text.call_args_list]
    assert "error" in sent_types
    assert room.state.get_player("p2").score == 0
    assert room.state.turn_index == 0


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


def test_play_card_choice_without_card_errors_cleanly() -> None:
    room = _card_chooser_room()
    ws1 = AsyncMock()
    room.connections.connect("p1", ws1)
    room.connections.connect("p2", AsyncMock())
    msg = PlayMsg(card_id="c1", placement=Placement(zone="center"), chosen_card_id=None)
    with patch("tbwc.agent.graph.interpret_card", return_value=_card_chooser_result()):
        asyncio.run(room.handle_action("p1", msg))
    # Clean error, no destruction, turn NOT advanced (no ValueError bubbling to 500).
    sent_types = [json.loads(c.args[0])["type"] for c in ws1.send_text.call_args_list]
    assert "error" in sent_types
    assert "t1" in room.state.get_player("p1").in_play
    assert room.state.turn_index == 0


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
