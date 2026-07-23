"""Bead t8b.2 — room-level consolation boon ladder at the fallback sites.

A card that can't be made to work no longer resolves as a bare note: the
ENGINE awards the card's AUTHOR a consolation boon "for trying". The boon
starts at consolation_points and, once the author's card_fallback count
reaches struggling_author_threshold, rotates through +2 points, draw 3
cards, and a one-shot score double. Seed cards, departed authors,
consolation_point_enabled=False, and the preview dry-run all keep today's
bare "no mechanical effect" note.
"""

from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, patch

from config import get_settings
from agent.contract import InterpretResult
from engine.history import append_history_event
from models.effects import AddPointsOp, CustomNoteOp, DrawCardsOp, SetPointsOp
from models.ws_messages import PlayMsg, PreviewCardMsg
from board.rooms.room import Room

INVALID = InterpretResult(program=None, snippet=None, verdict="invalid")


def _room_with_card(card: dict, *, hand_owner: str = "p1") -> Room:
    """Two-player playing room with ``card`` seeded into the owner's hand."""
    r = Room("ABCDEF")
    r.add_player("p1", "Alice")
    r.add_player("p2", "Bob")
    r.state = r.state.model_copy(update={"phase": "playing"})
    new_players = [
        p.model_copy(update={"hand": [card["id"]]}) if p.id == hand_owner else p.model_copy(update={"hand": ["other"]})
        for p in r.state.players
    ]
    r.state = r.state.model_copy(update={"cards": {card["id"]: card}, "players": new_players})
    r.connections.connect("p1", AsyncMock())
    r.connections.connect("p2", AsyncMock())
    return r


def _play_to_fallback(room: Room, card_id: str, actor: str = "p1") -> None:
    with patch("agent.runtime.run_agent", return_value=INVALID):
        asyncio.run(room.handle_action(actor, PlayMsg(card_id=card_id)))


def test_boon_goes_to_author_not_actor() -> None:
    card = {"id": "c1", "title": "Odd", "description": "???", "creator_id": "p2"}
    room = _room_with_card(card, hand_owner="p1")
    _play_to_fallback(room, "c1")
    assert room.state.get_player("p1").score == 0
    assert room.state.get_player("p2").score == 1
    assert any("for trying" in line for line in room.state.log)


def test_seed_card_gets_bare_note() -> None:
    card = {"id": "c2", "title": "Seed", "description": "???"}
    room = _room_with_card(card)
    _play_to_fallback(room, "c2")
    assert all(p.score == 0 for p in room.state.players)
    assert any("no mechanical effect" in line for line in room.state.log)
    assert not any("for trying" in line for line in room.state.log)


def test_departed_author_gets_bare_note() -> None:
    card = {"id": "c3", "title": "Orphan", "description": "???", "creator_id": "ghost"}
    room = _room_with_card(card)
    _play_to_fallback(room, "c3")
    assert all(p.score == 0 for p in room.state.players)
    assert any("no mechanical effect" in line for line in room.state.log)
    assert not any("for trying" in line for line in room.state.log)


def test_disabled_flag_keeps_bare_note(monkeypatch) -> None:
    monkeypatch.setenv("CONSOLATION_POINT_ENABLED", "false")
    get_settings.cache_clear()
    card = {"id": "c4", "title": "Nope", "description": "???", "creator_id": "p1"}
    room = _room_with_card(card)
    _play_to_fallback(room, "c4")
    assert all(p.score == 0 for p in room.state.players)
    assert any("no mechanical effect" in line for line in room.state.log)
    assert not any("for trying" in line for line in room.state.log)


def _ops_after_failures(n: int, *, author_score: int = 0) -> list:
    """_consolation_ops output once the author has ``n`` recorded fallbacks
    (the call sites record the current failure before building ops, so ``n``
    includes it)."""
    card = {"id": "cl", "title": "Flop", "description": "???", "creator_id": "p1"}
    room = _room_with_card(card)
    if author_score:
        room.state = room.state.model_copy(
            update={
                "players": [
                    p.model_copy(update={"score": author_score}) if p.id == "p1" else p for p in room.state.players
                ]
            }
        )
    for _ in range(n):
        room.state = append_history_event(
            room.state, "card_fallback", actor_id="p1", target_player_ids=["p1"], card_id="cl"
        )
    return room._consolation_ops(card, "cl")


def test_ladder_base_award_below_threshold() -> None:
    note, boon = _ops_after_failures(1)
    assert isinstance(note, CustomNoteOp)
    assert "no mechanical effect" in note.note
    assert "for trying" in note.note
    assert boon == AddPointsOp(target="id:p1", amount=1)


def test_ladder_escalates_and_rotates() -> None:
    assert _ops_after_failures(2)[1] == AddPointsOp(target="id:p1", amount=2)
    assert _ops_after_failures(3)[1] == DrawCardsOp(target="id:p1", amount=3)
    assert _ops_after_failures(4, author_score=5)[1] == SetPointsOp(target="id:p1", amount=10)
    assert _ops_after_failures(5)[1] == AddPointsOp(target="id:p1", amount=2)


def test_ladder_double_of_nonpositive_score_falls_back_to_points() -> None:
    assert _ops_after_failures(4)[1] == AddPointsOp(target="id:p1", amount=2)


def test_second_consecutive_failure_counts_itself() -> None:
    # End-to-end ordering pin: each call site records the current failure
    # (_set_card_mechanical_status) BEFORE building _consolation_ops, so the
    # author's second failed card sees a fallback count of 2 and lands on the
    # escalated +2 rung (default threshold 2) instead of repeating the flat +1.
    card_a = {"id": "f1", "title": "Flop One", "description": "???", "creator_id": "p1"}
    card_b = {"id": "f2", "title": "Flop Two", "description": "???", "creator_id": "p1"}
    room = _room_with_card(card_a, hand_owner="p1")
    room.state = room.state.model_copy(
        update={
            "cards": {**room.state.cards, "f2": card_b},
            "players": [
                p.model_copy(update={"hand": [*p.hand, "f2"]}) if p.id == "p2" else p for p in room.state.players
            ],
        }
    )
    _play_to_fallback(room, "f1", actor="p1")
    assert room.state.get_player("p1").score == 1
    _play_to_fallback(room, "f2", actor="p2")
    assert room.state.get_player("p1").score == 3
    assert any("+2 points" in line for line in room.state.log)


def test_preview_failure_awards_nothing() -> None:
    card = {"id": "seed", "title": "Filler", "description": "???"}
    room = _room_with_card(card)
    ws = AsyncMock()
    room.connections.connect("p1", ws)
    with patch("agent.runtime.run_agent", return_value=INVALID):
        asyncio.run(room._handle_preview_card("p1", PreviewCardMsg(title="Doomed", description="???")))
    assert all(p.score == 0 for p in room.state.players)
    sent = [json.loads(c.args[0]) for c in ws.send_text.call_args_list]
    result = next(m for m in sent if m["type"] == "preview_result")
    assert result["mechanical_status"] == "fallback"
    assert "add_points" not in json.dumps(result)
