"""Scry (card_order) and draw-N-keep-1 (card_pick from_deck_top) — bead b8x.3.

The deck's contents and order are hidden information: these tests pin that the
offered top-N ids/faces reach exactly the interaction's audience (WS descriptor
and redacted snapshot registry), that responses are validated as permutations /
picks of the offered set, and that the resume snippet's write-back produces the
requested deck order.
"""

from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock

import pytest
from pydantic import ValidationError

from models.effects import ResolutionPlan
from models.interactions import CardOrderInteraction, CardOrderResponse, CardPickInteraction
from models.ws_messages import InteractionResponseMsg, PlayMsg
from board.rooms.room import Room

SCRY_SNIPPET = """def apply(state, ctx):
    seq = ctx['interactions']['scry'][ctx['actor_id']]
    for cid in seq['to_bottom']:
        state.move_cards(card_target='id:' + cid, to_zone='deck', to_position='bottom')
    for cid in reversed(seq['order']):
        state.move_cards(card_target='id:' + cid, to_zone='deck', to_position='top')
"""

KEEP_ONE_SNIPPET = """def apply(state, ctx):
    picked = ctx['interactions']['pick'][ctx['actor_id']]
    if picked:
        state.move_cards(card_target='id:' + picked, to_zone='hand', to_player='id:' + ctx['actor_id'])
        state.move_cards(from_zone='deck', selector='top', count=2, to_zone='deck', to_position='bottom')
"""


def _scry_plan(count: int = 3) -> ResolutionPlan:
    return ResolutionPlan.model_validate(
        {
            "steps": [
                {
                    "kind": "interaction",
                    "result_key": "scry",
                    "request": {"kind": "card_order", "prompt": "Reorder the top of the deck", "count": count},
                },
                {"kind": "snippet", "code": SCRY_SNIPPET},
            ]
        }
    )


KEEP_EACH_SNIPPET = """def apply(state, ctx):
    for pid, picked in ctx['interactions']['pick'].items():
        if picked:
            state.move_cards(card_target='id:' + picked, to_zone='hand', to_player='id:' + pid)
"""


def _everyone_keeps_one_plan() -> ResolutionPlan:
    return ResolutionPlan.model_validate(
        {
            "steps": [
                {
                    "kind": "interaction",
                    "result_key": "pick",
                    "request": {
                        "kind": "card_pick",
                        "prompt": "Everyone keeps one",
                        "from_deck_top": 3,
                        "audience": "all",
                    },
                },
                {"kind": "snippet", "code": KEEP_EACH_SNIPPET},
            ]
        }
    )


def _keep_one_plan() -> ResolutionPlan:
    return ResolutionPlan.model_validate(
        {
            "steps": [
                {
                    "kind": "interaction",
                    "result_key": "pick",
                    "request": {"kind": "card_pick", "prompt": "Keep one", "from_deck_top": 3},
                },
                {"kind": "snippet", "code": KEEP_ONE_SNIPPET},
            ]
        }
    )


def _room_with_plan(plan: ResolutionPlan) -> Room:
    room = Room("SCRYRM")
    room.add_player("p1", "Alice")
    room.add_player("p2", "Bob")
    cards = {
        "card": {
            "id": "card",
            "title": "Scry Card",
            "description": "Peek at the deck.",
            "canonical": {"steps": [step.model_dump(mode="json") for step in plan.steps]},
        }
    }
    for i in range(1, 5):
        cards[f"d{i}"] = {"id": f"d{i}", "title": f"Deck Card {i}", "description": f"Hidden {i}."}
    players = [
        player.model_copy(update={"hand": ["card"]}) if player.id == "p1" else player for player in room.state.players
    ]
    # draw=0 keeps the post-resolution turn advance from auto-drawing the deck
    # top, so assertions see exactly the order the write-back produced.
    room.state = room.state.model_copy(
        update={
            "phase": "playing",
            "cards": cards,
            "players": players,
            "deck": ["d1", "d2", "d3", "d4"],
            "rules": room.state.rules.model_copy(update={"draw": 0}),
        }
    )
    room.connections.connect("p1", AsyncMock())
    room.connections.connect("p2", AsyncMock())
    return room


def _messages(room: Room, player_id: str) -> list[dict]:
    return [json.loads(call.args[0]) for call in room.connections._connections[player_id].send_text.call_args_list]


def _requests(room: Room, player_id: str) -> list[dict]:
    return [message for message in _messages(room, player_id) if message["type"] == "interaction_request"]


def _response(interaction_id: str, kind: str, **payload) -> InteractionResponseMsg:
    return InteractionResponseMsg.model_validate(
        {"interaction_id": interaction_id, "payload": {"kind": kind, **payload}}
    )


# ── descriptor models ──


def test_card_order_descriptor_defaults() -> None:
    request = CardOrderInteraction(prompt="Scry", count=3)
    assert request.source == "deck_top"
    assert request.audience == "active"
    assert request.sealed is True


def test_card_order_response_rejects_duplicates_across_split() -> None:
    with pytest.raises(ValidationError):
        CardOrderResponse(order=["d1", "d2"], to_bottom=["d1"])


def test_card_pick_rejects_from_hand_with_from_deck_top() -> None:
    with pytest.raises(ValidationError):
        CardPickInteraction(prompt="Pick", from_hand=True, from_deck_top=3)


def test_plan_accepts_card_pick_with_only_from_deck_top() -> None:
    plan = _keep_one_plan()
    step = plan.steps[0]
    assert step.request.from_deck_top == 3
    assert step.request.card_ids == []


# ── audience-only materialization ──


def test_card_order_descriptor_fills_ids_and_faces_for_audience_only() -> None:
    room = _room_with_plan(_scry_plan())
    asyncio.run(room.handle_action("p1", PlayMsg(card_id="card")))

    pending = room._pending_resolution
    assert pending is not None
    assert pending.resolved_audience == ["p1"]

    requests = _requests(room, "p1")
    assert requests
    descriptor = requests[-1]["descriptor"]
    assert descriptor["card_ids"] == ["d1", "d2", "d3"]
    assert {cid: card["title"] for cid, card in descriptor["cards"].items()} == {
        "d1": "Deck Card 1",
        "d2": "Deck Card 2",
        "d3": "Deck Card 3",
    }
    assert _requests(room, "p2") == []

    # The stored descriptor itself never carries materialized ids or faces.
    dumped = pending.request.model_dump()
    assert "cards" not in dumped
    assert "card_ids" not in dumped


def test_pending_scry_registry_visible_to_audience_viewer_only() -> None:
    room = _room_with_plan(_scry_plan())
    asyncio.run(room.handle_action("p1", PlayMsg(card_id="card")))

    audience_view = room.snapshot_for("p1")
    other_view = room.snapshot_for("p2")
    spectator_view = room.snapshot_for(None)
    assert {"d1", "d2", "d3"} <= set(audience_view["cards"])
    assert "d4" not in audience_view["cards"]
    for view in (other_view, spectator_view):
        assert not {"d1", "d2", "d3", "d4"} & set(view["cards"])
    for view in (audience_view, other_view, spectator_view):
        assert "interaction_card_visibility" not in view
        assert view["deck"] == []

    # Shared (per-viewer identical) fields never name the offered cards.
    shared = room.snapshot()["pending_interaction"]
    assert "card_ids" not in json.dumps(shared)


def test_registry_visibility_ends_when_interaction_resolves() -> None:
    room = _room_with_plan(_scry_plan())

    async def scenario() -> None:
        await room.handle_action("p1", PlayMsg(card_id="card"))
        interaction_id = room._pending_resolution.interaction_id
        await room.handle_action("p1", _response(interaction_id, "card_order", order=["d1", "d2", "d3"], to_bottom=[]))

    asyncio.run(scenario())
    assert room._pending_resolution is None
    assert not {"d1", "d2", "d3"} & set(room.snapshot_for("p1")["cards"])


# ── response validation ──


def test_card_order_rejects_foreign_and_partial_permutations() -> None:
    room = _room_with_plan(_scry_plan())

    async def scenario() -> None:
        await room.handle_action("p1", PlayMsg(card_id="card"))
        interaction_id = room._pending_resolution.interaction_id
        # d4 is not among the offered top three.
        await room.handle_action("p1", _response(interaction_id, "card_order", order=["d1", "d2", "d4"], to_bottom=[]))
        assert room._pending_resolution is not None
        assert "p1" not in room._pending_resolution.responses
        # Dropping a card is not a permutation either.
        await room.handle_action("p1", _response(interaction_id, "card_order", order=["d1", "d2"]))
        assert room._pending_resolution is not None
        assert "p1" not in room._pending_resolution.responses

    asyncio.run(scenario())
    errors = [message for message in _messages(room, "p1") if message["type"] == "error"]
    assert len(errors) == 2
    assert all("permutation" in message["message"] for message in errors)


def test_non_audience_response_is_rejected() -> None:
    room = _room_with_plan(_scry_plan())

    async def scenario() -> None:
        await room.handle_action("p1", PlayMsg(card_id="card"))
        interaction_id = room._pending_resolution.interaction_id
        await room.handle_action("p2", _response(interaction_id, "card_order", order=["d3", "d2", "d1"], to_bottom=[]))

    asyncio.run(scenario())
    assert room._pending_resolution is not None
    assert room._pending_resolution.responses == {}
    errors = [message for message in _messages(room, "p2") if message["type"] == "error"]
    assert errors


# ── write-back ──


def test_scry_write_back_sets_order_and_bottoms_split() -> None:
    room = _room_with_plan(_scry_plan())

    async def scenario() -> None:
        await room.handle_action("p1", PlayMsg(card_id="card"))
        interaction_id = room._pending_resolution.interaction_id
        await room.handle_action("p1", _response(interaction_id, "card_order", order=["d3", "d1"], to_bottom=["d2"]))

    asyncio.run(scenario())
    assert room._pending_resolution is None
    assert room.state.deck == ["d3", "d1", "d4", "d2"]
    assert room.state.discard == ["card"]
    assert room.state.cards["card"].get("mechanical_status") != "fallback"
    # The shared log must not pin deck positions to the reordered ids.
    assert not any("id:d" in line for line in room.state.log)


def test_scry_identity_order_keeps_deck_unchanged() -> None:
    room = _room_with_plan(_scry_plan())

    async def scenario() -> None:
        await room.handle_action("p1", PlayMsg(card_id="card"))
        interaction_id = room._pending_resolution.interaction_id
        await room.handle_action("p1", _response(interaction_id, "card_order", order=["d1", "d2", "d3"], to_bottom=[]))

    asyncio.run(scenario())
    assert room.state.deck == ["d1", "d2", "d3", "d4"]


def test_from_deck_top_pick_keeps_one_and_bottoms_rest() -> None:
    room = _room_with_plan(_keep_one_plan())

    async def scenario() -> None:
        await room.handle_action("p1", PlayMsg(card_id="card"))
        descriptor = _requests(room, "p1")[-1]["descriptor"]
        assert descriptor["card_ids"] == ["d1", "d2", "d3"]
        assert set(descriptor["cards"]) == {"d1", "d2", "d3"}
        interaction_id = room._pending_resolution.interaction_id
        await room.handle_action("p1", _response(interaction_id, "card_pick", card_id="d2"))

    asyncio.run(scenario())
    assert room._pending_resolution is None
    assert room.state.get_player("p1").hand == ["d2"]
    assert room.state.deck == ["d4", "d1", "d3"]
    assert not any("id:d" in line for line in room.state.log)


def test_from_deck_top_pick_is_first_come_unique_across_the_audience() -> None:
    """The deck top is one shared pool: a card one audience member claimed
    cannot be claimed (or silently stolen) by another."""
    room = _room_with_plan(_everyone_keeps_one_plan())

    async def scenario() -> None:
        await room.handle_action("p1", PlayMsg(card_id="card"))
        assert room._pending_resolution.resolved_audience == ["p1", "p2"]
        interaction_id = room._pending_resolution.interaction_id
        await room.handle_action("p1", _response(interaction_id, "card_pick", card_id="d2"))
        # p2's refreshed options no longer offer p1's claim.
        descriptor = _requests(room, "p2")[-1]["descriptor"]
        assert descriptor["card_ids"] == ["d1", "d3"]
        assert set(descriptor["cards"]) == {"d1", "d3"}
        # Claiming the same card anyway is rejected, not silently reassigned.
        await room.handle_action("p2", _response(interaction_id, "card_pick", card_id="d2"))
        assert room._pending_resolution is not None
        assert "p2" not in room._pending_resolution.responses
        await room.handle_action("p2", _response(interaction_id, "card_pick", card_id="d1"))

    asyncio.run(scenario())
    errors = [message for message in _messages(room, "p2") if message["type"] == "error"]
    assert any("already taken" in message["message"] for message in errors)
    assert room._pending_resolution is None
    assert room.state.get_player("p1").hand == ["d2"]
    assert room.state.get_player("p2").hand == ["d1"]
    assert room.state.deck == ["d3", "d4"]


def test_from_deck_top_rejects_card_below_the_offered_top() -> None:
    room = _room_with_plan(_keep_one_plan())

    async def scenario() -> None:
        await room.handle_action("p1", PlayMsg(card_id="card"))
        interaction_id = room._pending_resolution.interaction_id
        await room.handle_action("p1", _response(interaction_id, "card_pick", card_id="d4"))

    asyncio.run(scenario())
    assert room._pending_resolution is not None
    assert room.state.deck == ["d1", "d2", "d3", "d4"]
    errors = [message for message in _messages(room, "p1") if message["type"] == "error"]
    assert errors


# ── timeout ──


def test_scry_timeout_with_no_response_falls_back_and_keeps_deck() -> None:
    room = _room_with_plan(_scry_plan())

    async def scenario() -> None:
        await room.handle_action("p1", PlayMsg(card_id="card"))
        room._interaction_timer = asyncio.current_task()
        await room._resume_pending_resolution(timed_out=True)

    asyncio.run(scenario())
    assert room._pending_resolution is None
    assert room.state.deck == ["d1", "d2", "d3", "d4"]
    assert room.state.discard == ["card"]
    assert room.state.cards["card"]["mechanical_status"] == "fallback"


def test_default_card_order_value_is_identity_order() -> None:
    room = _room_with_plan(_scry_plan())
    asyncio.run(room.handle_action("p1", PlayMsg(card_id="card")))
    value = room._default_interaction_value(room._pending_resolution.request)
    assert value == {"order": ["d1", "d2", "d3"], "to_bottom": []}


# ── reconnect while pending ──


def test_reconnect_replays_filled_descriptor_to_audience_only() -> None:
    room = _room_with_plan(_scry_plan())

    async def scenario() -> None:
        await room.handle_action("p1", PlayMsg(card_id="card"))
        room.connections._connections["p1"].send_text.reset_mock()
        room.connections._connections["p2"].send_text.reset_mock()
        await room.replay_pending_interaction("p1")
        await room.replay_pending_interaction("p2")

    asyncio.run(scenario())
    requests = _requests(room, "p1")
    assert len(requests) == 1
    assert requests[0]["descriptor"]["card_ids"] == ["d1", "d2", "d3"]
    assert set(requests[0]["descriptor"]["cards"]) == {"d1", "d2", "d3"}
    assert _requests(room, "p2") == []
    assert {"d1", "d2", "d3"} <= set(room.snapshot_for("p1")["cards"])
    assert not {"d1", "d2", "d3"} & set(room.snapshot_for("p2")["cards"])


def test_pending_scry_survives_store_round_trip(tmp_path) -> None:
    from board.rooms.store import FileRoomStore

    store = FileRoomStore(tmp_path)
    room = _room_with_plan(_scry_plan())
    asyncio.run(room.handle_action("p1", PlayMsg(card_id="card")))
    store.put(room.code, room)

    restored = FileRoomStore(tmp_path).get(room.code)
    assert restored is not None
    pending = restored._pending_resolution
    assert pending is not None
    assert isinstance(pending.request, CardOrderInteraction)
    restored.connections.connect("p1", AsyncMock())

    async def respond() -> None:
        await restored.handle_action(
            "p1", _response(pending.interaction_id, "card_order", order=["d2", "d3", "d1"], to_bottom=[])
        )

    asyncio.run(respond())
    assert restored._pending_resolution is None
    assert restored.state.deck == ["d2", "d3", "d1", "d4"]


# ── short deck ──


def test_scry_offers_only_what_the_deck_holds() -> None:
    room = _room_with_plan(_scry_plan())
    room.state = room.state.model_copy(update={"deck": ["d1", "d2"]})

    async def scenario() -> None:
        await room.handle_action("p1", PlayMsg(card_id="card"))
        descriptor = _requests(room, "p1")[-1]["descriptor"]
        assert descriptor["card_ids"] == ["d1", "d2"]
        interaction_id = room._pending_resolution.interaction_id
        await room.handle_action("p1", _response(interaction_id, "card_order", order=["d2"], to_bottom=["d1"]))

    asyncio.run(scenario())
    assert room._pending_resolution is None
    assert room.state.deck == ["d2", "d1"]
