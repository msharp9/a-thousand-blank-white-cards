from __future__ import annotations

import asyncio
import json
import pathlib
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import agent.triage as triage_module
from config import get_settings
from models.effects import ResolutionPlan
from models.ws_messages import InteractionResponseMsg, PlayMsg
from board.rooms.room import Room
from board.rooms.store import FileRoomStore
from board.rooms.manager import RoomManager

DATA_DIR = pathlib.Path(__file__).parent.parent / "data"


def _gold_plan(title: str) -> ResolutionPlan:
    cards = json.loads((DATA_DIR / "seed_cards_gold.json").read_text())
    card = next(card for card in cards if card["title"] == title)
    return ResolutionPlan.model_validate({"steps": card["canonical"]["steps"]})


def _room_with_plan(plan: ResolutionPlan) -> Room:
    room = Room("INTERA")
    room.add_player("p1", "Alice")
    room.add_player("p2", "Bob")
    card = {
        "id": "card",
        "title": "Interactive",
        "description": "Ask the table.",
        "canonical": {"steps": [step.model_dump(mode="json") for step in plan.steps]},
    }
    players = [
        player.model_copy(update={"hand": ["card"]}) if player.id == "p1" else player for player in room.state.players
    ]
    room.state = room.state.model_copy(
        update={"phase": "playing", "cards": {"card": card}, "players": players, "deck": []}
    )
    room.connections.connect("p1", AsyncMock())
    room.connections.connect("p2", AsyncMock())
    return room


def _response(interaction_id: str, kind: str, **payload) -> InteractionResponseMsg:
    return InteractionResponseMsg.model_validate(
        {"interaction_id": interaction_id, "payload": {"kind": kind, **payload}}
    )


def test_sealed_auction_pauses_atomically_and_resumes_once() -> None:
    plan = _gold_plan("Going Once, Going Twice")
    room = _room_with_plan(plan)
    room.state = room.state.model_copy(
        update={"players": [player.model_copy(update={"score": 10}) for player in room.state.players]}
    )

    async def scenario() -> None:
        await room.handle_action("p1", PlayMsg(card_id="card"))
        pending = room._pending_resolution
        assert pending is not None
        interaction_id = pending.interaction_id
        assert room.state.get_player("p1").score == 10
        assert "card" in room.state.get_player("p1").hand
        assert "responses" not in room.snapshot()["pending_interaction"]

        await room.handle_action("p1", _response(interaction_id, "number", value=3))
        assert room.state.get_player("p1").score == 10
        assert room._pending_resolution is not None
        await room.handle_action("p2", _response(interaction_id, "number", value=5))

    asyncio.run(scenario())
    assert room._pending_resolution is None
    assert [player.score for player in room.state.players] == [10, 5]
    assert room.state.get_player("p2").hand == ["card"]
    assert room.state.get_player("p1").hand == []
    assert room.state.discard == []
    assert room.state.turn_index == 1
    messages = [json.loads(call.args[0]) for call in room.connections._connections["p1"].send_text.call_args_list]
    progress = [message for message in messages if message["type"] == "interaction_progress"]
    assert progress and all("responses" not in message for message in progress)


def test_auction_tie_uses_effective_turn_order() -> None:
    plan = _gold_plan("Going Once, Going Twice")
    room = _room_with_plan(plan)
    room.state = room.state.model_copy(
        update={
            "turn_order": ["p2", "p1"],
            "players": [player.model_copy(update={"score": 10}) for player in room.state.players],
        }
    )

    async def scenario() -> None:
        await room.handle_action("p1", PlayMsg(card_id="card"))
        interaction_id = room._pending_resolution.interaction_id
        await room.handle_action("p1", _response(interaction_id, "number", value=5))
        await room.handle_action("p2", _response(interaction_id, "number", value=5))

    asyncio.run(scenario())
    assert [player.score for player in room.state.players] == [10, 5]
    assert room.state.get_player("p2").hand == ["card"]


def test_invalid_interaction_audience_falls_back_atomically() -> None:
    plan = ResolutionPlan.model_validate(
        {
            "steps": [
                {"kind": "ops", "ops": [{"op": "add_points", "target": "self", "amount": 10}]},
                {
                    "kind": "interaction",
                    "result_key": "missing",
                    "request": {"kind": "confirm", "prompt": "Answer", "audience": "player:missing"},
                },
            ]
        }
    )
    room = _room_with_plan(plan)
    asyncio.run(room.handle_action("p1", PlayMsg(card_id="card")))
    assert room._pending_resolution is None
    assert room.state.get_player("p1").score == 0
    assert room.state.discard == ["card"]
    assert room.state.cards["card"]["mechanical_status"] == "fallback"


def test_invalid_chained_ref_falls_back_atomically() -> None:
    plan = ResolutionPlan.model_validate(
        {
            "steps": [
                {"kind": "ops", "ops": [{"op": "add_points", "target": "self", "amount": 10}]},
                {
                    "kind": "interaction",
                    "result_key": "answers",
                    "request": {"kind": "confirm", "prompt": "Continue?"},
                },
                {
                    "kind": "interaction",
                    "result_key": "vote",
                    "request": {"kind": "choice", "prompt": "Vote"},
                    "input_refs": {"options": {"result_key": "answers", "path": ["missing"]}},
                },
            ]
        }
    )
    room = _room_with_plan(plan)

    async def scenario() -> None:
        await room.handle_action("p1", PlayMsg(card_id="card"))
        await room.handle_action("p1", _response(room._pending_resolution.interaction_id, "confirm", confirmed=True))

    asyncio.run(scenario())
    assert room._pending_resolution is None
    assert room.state.get_player("p1").score == 0
    assert room.state.discard == ["card"]
    assert room.state.cards["card"]["mechanical_status"] == "fallback"


def test_dynamic_choice_revalidates_selection_bounds() -> None:
    plan = ResolutionPlan.model_validate(
        {
            "steps": [
                {
                    "kind": "interaction",
                    "result_key": "submission",
                    "request": {"kind": "text", "prompt": "Submit", "audience": "active"},
                },
                {
                    "kind": "interaction",
                    "result_key": "vote",
                    "request": {
                        "kind": "choice",
                        "prompt": "Pick two",
                        "min_selections": 2,
                        "max_selections": 2,
                    },
                    "input_refs": {"options": {"result_key": "submission"}},
                },
            ]
        }
    )
    room = _room_with_plan(plan)

    async def scenario() -> None:
        await room.handle_action("p1", PlayMsg(card_id="card"))
        await room.handle_action("p1", _response(room._pending_resolution.interaction_id, "text", value="only option"))

    asyncio.run(scenario())
    assert room._pending_resolution is None
    assert room.state.cards["card"]["mechanical_status"] == "fallback"


def test_dynamic_choice_requires_object_in_live_resolution() -> None:
    plan = ResolutionPlan.model_validate(
        {
            "steps": [
                {
                    "kind": "interaction",
                    "result_key": "submission",
                    "request": {"kind": "text", "prompt": "Submit", "audience": "active"},
                },
                {
                    "kind": "interaction",
                    "result_key": "vote",
                    "request": {"kind": "choice", "prompt": "Vote"},
                    "input_refs": {"options": {"result_key": "submission", "path": ["p1"]}},
                },
            ]
        }
    )
    room = _room_with_plan(plan)

    async def scenario() -> None:
        await room.handle_action("p1", PlayMsg(card_id="card"))
        await room.handle_action(
            "p1", _response(room._pending_resolution.interaction_id, "text", value="not an object")
        )

    asyncio.run(scenario())
    assert room._pending_resolution is None
    assert room.state.cards["card"]["mechanical_status"] == "fallback"


def test_response_at_or_after_deadline_is_not_accepted() -> None:
    plan = ResolutionPlan.model_validate(
        {
            "steps": [
                {
                    "kind": "interaction",
                    "result_key": "answer",
                    "request": {"kind": "confirm", "prompt": "Continue?", "audience": "all"},
                }
            ]
        }
    )
    room = _room_with_plan(plan)

    async def scenario() -> None:
        await room.handle_action("p1", PlayMsg(card_id="card"))
        pending = room._pending_resolution
        pending.deadline_at = datetime.now(UTC) - timedelta(milliseconds=1)
        await room.handle_action("p1", _response(pending.interaction_id, "confirm", confirmed=True))

    asyncio.run(scenario())
    assert room._pending_resolution is None
    assert room.state.cards["card"]["mechanical_status"] == "fallback"


def test_from_hand_pick_lets_everyone_discard_a_card_they_choose() -> None:
    """End-to-end 'everyone discards a card they choose': a from_hand card_pick
    shows each player THEIR OWN hand, and the following snippet destroys each
    player's pick. This is the path a plain destroy_card cannot express."""
    plan = ResolutionPlan.model_validate(
        {
            "steps": [
                {
                    "kind": "interaction",
                    "result_key": "discards",
                    "request": {"kind": "card_pick", "prompt": "Discard a card", "audience": "all", "from_hand": True},
                },
                {
                    "kind": "snippet",
                    "code": (
                        "def apply(state, ctx):\n"
                        "    for cid in ctx['interactions']['discards'].values():\n"
                        "        if cid:\n"
                        "            state.destroy_card(card_id=cid)\n"
                    ),
                },
            ]
        }
    )
    room = _room_with_plan(plan)
    # p1 holds the played card plus a discardable one; p2 holds two.
    cards = {
        **room.state.cards,
        "p1a": {"id": "p1a", "title": "P1-A"},
        "p2a": {"id": "p2a", "title": "P2-A"},
        "p2b": {"id": "p2b", "title": "P2-B"},
    }
    players = [
        p.model_copy(update={"hand": ["card", "p1a"]})
        if p.id == "p1"
        else p.model_copy(update={"hand": ["p2a", "p2b"]})
        for p in room.state.players
    ]
    room.state = room.state.model_copy(update={"cards": cards, "players": players})

    sent: dict[str, list] = {"p1": [], "p2": []}

    async def scenario() -> None:
        await room.handle_action("p1", PlayMsg(card_id="card"))
        pending = room._pending_resolution
        assert pending is not None and pending.request.from_hand is True
        interaction_id = pending.interaction_id
        # Each player is offered only their own hand (played "card" already left p1's hand).
        for pid in ("p1", "p2"):
            messages = [json.loads(c.args[0]) for c in room.connections._connections[pid].send_text.call_args_list]
            reqs = [m for m in messages if m["type"] == "interaction_request"]
            sent[pid] = reqs[-1]["descriptor"]["card_ids"]
        # p1 discards p1a; p2 discards p2b.
        await room.handle_action("p1", _response(interaction_id, "card_pick", card_id="p1a"))
        await room.handle_action("p2", _response(interaction_id, "card_pick", card_id="p2b"))

    asyncio.run(scenario())
    assert sent["p1"] == ["p1a"]  # played card excluded; own hand only
    assert set(sent["p2"]) == {"p2a", "p2b"}
    assert room._pending_resolution is None
    assert room.state.get_player("p1").hand == []
    assert room.state.get_player("p2").hand == ["p2a"]
    assert "p1a" in room.state.discard and "p2b" in room.state.discard


def test_from_hand_pick_rejects_a_card_not_in_the_responders_hand() -> None:
    plan = ResolutionPlan.model_validate(
        {
            "steps": [
                {
                    "kind": "interaction",
                    "result_key": "discards",
                    "request": {"kind": "card_pick", "prompt": "Discard", "audience": "all", "from_hand": True},
                },
                {"kind": "snippet", "code": "def apply(state, ctx):\n    return None\n"},
            ]
        }
    )
    room = _room_with_plan(plan)
    players = [
        p.model_copy(update={"hand": ["card"]}) if p.id == "p1" else p.model_copy(update={"hand": ["p2a"]})
        for p in room.state.players
    ]
    room.state = room.state.model_copy(
        update={"cards": {**room.state.cards, "p2a": {"id": "p2a", "title": "P2-A"}}, "players": players}
    )

    async def scenario() -> None:
        await room.handle_action("p1", PlayMsg(card_id="card"))
        interaction_id = room._pending_resolution.interaction_id
        # p2 tries to discard a card that isn't theirs — must be rejected.
        await room.handle_action("p2", _response(interaction_id, "card_pick", card_id="p1a"))
        assert room._pending_resolution is not None  # invalid response did not resolve
        assert "p2" not in room._pending_resolution.responses

    asyncio.run(scenario())


def test_from_hand_multi_pick_lets_each_player_discard_n_cards() -> None:
    """'Everyone discards 2 cards they choose': a from_hand card_pick with
    max_picks=2 collects a LIST per player, which the snippet destroys."""
    plan = ResolutionPlan.model_validate(
        {
            "steps": [
                {
                    "kind": "interaction",
                    "result_key": "discards",
                    "request": {
                        "kind": "card_pick",
                        "prompt": "Discard 2 cards",
                        "audience": "all",
                        "from_hand": True,
                        "min_picks": 2,
                        "max_picks": 2,
                    },
                },
                {
                    "kind": "snippet",
                    "code": (
                        "def apply(state, ctx):\n"
                        "    for picks in ctx['interactions']['discards'].values():\n"
                        "        for cid in picks:\n"
                        "            state.destroy_card(card_id=cid)\n"
                    ),
                },
            ]
        }
    )
    room = _room_with_plan(plan)
    cards = {
        **room.state.cards,
        **{cid: {"id": cid, "title": cid} for cid in ("p1a", "p1b", "p1c", "p2a", "p2b")},
    }
    players = [
        p.model_copy(update={"hand": ["card", "p1a", "p1b", "p1c"]})
        if p.id == "p1"
        else p.model_copy(update={"hand": ["p2a", "p2b"]})
        for p in room.state.players
    ]
    room.state = room.state.model_copy(update={"cards": cards, "players": players})

    async def scenario() -> None:
        await room.handle_action("p1", PlayMsg(card_id="card"))
        interaction_id = room._pending_resolution.interaction_id
        await room.handle_action("p1", _response(interaction_id, "card_pick", card_ids=["p1a", "p1c"]))
        await room.handle_action("p2", _response(interaction_id, "card_pick", card_ids=["p2a", "p2b"]))

    asyncio.run(scenario())
    assert room._pending_resolution is None
    assert room.state.get_player("p1").hand == ["p1b"]  # p1a + p1c discarded
    assert room.state.get_player("p2").hand == []  # both discarded
    assert {"p1a", "p1c", "p2a", "p2b"} <= set(room.state.discard)


def test_from_hand_multi_pick_rejects_wrong_count() -> None:
    plan = ResolutionPlan.model_validate(
        {
            "steps": [
                {
                    "kind": "interaction",
                    "result_key": "discards",
                    "request": {
                        "kind": "card_pick",
                        "prompt": "Discard 2",
                        "audience": "all",
                        "from_hand": True,
                        "min_picks": 2,
                        "max_picks": 2,
                    },
                },
                {"kind": "snippet", "code": "def apply(state, ctx):\n    return None\n"},
            ]
        }
    )
    room = _room_with_plan(plan)
    players = [
        p.model_copy(update={"hand": ["card", "p1a", "p1b"]})
        if p.id == "p1"
        else p.model_copy(update={"hand": ["p2a", "p2b"]})
        for p in room.state.players
    ]
    room.state = room.state.model_copy(
        update={
            "cards": {**room.state.cards, **{cid: {"id": cid, "title": cid} for cid in ("p1a", "p1b", "p2a", "p2b")}},
            "players": players,
        }
    )

    async def scenario() -> None:
        await room.handle_action("p1", PlayMsg(card_id="card"))
        interaction_id = room._pending_resolution.interaction_id
        # Only one card when two are required — rejected, response not recorded.
        await room.handle_action("p1", _response(interaction_id, "card_pick", card_ids=["p1a"]))
        assert room._pending_resolution is not None
        assert "p1" not in room._pending_resolution.responses

    asyncio.run(scenario())


def test_zero_response_timeout_rolls_back_prefix_and_consumes_visible_noop() -> None:
    plan = ResolutionPlan.model_validate(
        {
            "steps": [
                {"kind": "ops", "ops": [{"op": "add_points", "target": "self", "amount": 10}]},
                {
                    "kind": "interaction",
                    "result_key": "answer",
                    "request": {"kind": "confirm", "prompt": "Accept?", "audience": "all"},
                },
            ]
        }
    )
    room = _room_with_plan(plan)

    async def scenario() -> None:
        await room.handle_action("p1", PlayMsg(card_id="card"))
        assert room.state.get_player("p1").score == 0
        room._interaction_timer = asyncio.current_task()
        await room._resume_pending_resolution(timed_out=True)

    asyncio.run(scenario())
    assert room.state.get_player("p1").score == 0
    assert room.state.discard == ["card"]
    assert any("No one responded" in line for line in room.state.log)
    assert room.state.cards["card"]["mechanical_status"] == "fallback"


def test_partial_timeout_uses_submitted_values_and_defaults() -> None:
    plan = ResolutionPlan.model_validate(
        {
            "steps": [
                {
                    "kind": "interaction",
                    "result_key": "bids",
                    "request": {"kind": "number", "prompt": "Bid", "audience": "all", "minimum": 0},
                },
                {
                    "kind": "snippet",
                    "code": "def apply(state, ctx):\n    state.add_points('self', int(sum(ctx['interactions']['bids'].values())))\n",
                },
            ]
        }
    )
    room = _room_with_plan(plan)

    async def scenario() -> None:
        await room.handle_action("p1", PlayMsg(card_id="card"))
        interaction_id = room._pending_resolution.interaction_id
        await room.handle_action("p1", _response(interaction_id, "number", value=3))
        await room._resume_pending_resolution(timed_out=True)

    asyncio.run(scenario())
    assert room.state.get_player("p1").score == 3


def test_drawing_then_vote_materializes_sealed_submissions_and_tied_winners() -> None:
    plan = _gold_plan("Cat Show")
    room = _room_with_plan(plan)
    stroke = [{"points": [{"x": 0.1, "y": 0.2}, {"x": 0.8, "y": 0.9}]}]

    async def scenario() -> None:
        await room.handle_action("p1", PlayMsg(card_id="card"))
        drawing_id = room._pending_resolution.interaction_id
        await room.handle_action("p1", _response(drawing_id, "drawing", strokes=stroke))
        await room.handle_action("p2", _response(drawing_id, "drawing", strokes=stroke))
        vote = room._pending_resolution
        assert vote is not None and vote.request.kind == "choice"
        assert {option.id for option in vote.request.options} == {"p1", "p2"}
        assert all(option.payload for option in vote.request.options)
        await room.handle_action("p1", _response(vote.interaction_id, "choice", option_ids=["p1"]))
        await room.handle_action("p2", _response(vote.interaction_id, "choice", option_ids=["p2"]))

    asyncio.run(scenario())
    assert [player.score for player in room.state.players] == [3, 3]
    assert room._pending_resolution is None


def test_cat_show_vote_stage_survives_cold_restore_and_completes(tmp_path) -> None:
    room = _room_with_plan(_gold_plan("Cat Show"))
    stroke = [{"points": [{"x": 0.1, "y": 0.2}, {"x": 0.8, "y": 0.9}]}]

    async def reach_vote() -> None:
        await room.handle_action("p1", PlayMsg(card_id="card"))
        drawing_id = room._pending_resolution.interaction_id
        await room.handle_action("p1", _response(drawing_id, "drawing", strokes=stroke))
        await room.handle_action("p2", _response(drawing_id, "drawing", strokes=stroke))

    asyncio.run(reach_vote())
    assert room._pending_resolution is not None
    assert room._pending_resolution.request.kind == "choice"
    assert set(room._pending_resolution.interactions["cats"]) == {"p1", "p2"}

    store = FileRoomStore(tmp_path)
    store.put(room.code, room)
    restored = FileRoomStore(tmp_path).get(room.code)
    assert restored is not None and restored._pending_resolution is not None
    assert restored._pending_resolution.request.kind == "choice"
    assert set(restored._pending_resolution.interactions["cats"]) == {"p1", "p2"}
    restored.connections.connect("p1", AsyncMock())
    restored.connections.connect("p2", AsyncMock())

    async def finish_vote() -> None:
        vote_id = restored._pending_resolution.interaction_id
        await restored.handle_action("p1", _response(vote_id, "choice", option_ids=["p1"]))
        await restored.handle_action("p2", _response(vote_id, "choice", option_ids=["p2"]))

    asyncio.run(finish_vote())
    assert restored._pending_resolution is None
    assert [player.score for player in restored.state.players] == [3, 3]


def test_pending_resolution_persists_and_request_replays_without_values(tmp_path) -> None:
    plan = ResolutionPlan.model_validate(
        {
            "steps": [
                {
                    "kind": "interaction",
                    "result_key": "secret",
                    "request": {"kind": "text", "prompt": "Secret", "audience": "all", "sealed": True},
                }
            ]
        }
    )
    room = _room_with_plan(plan)

    async def pause() -> None:
        await room.handle_action("p1", PlayMsg(card_id="card"))
        await room.handle_action(
            "p1",
            _response(room._pending_resolution.interaction_id, "text", value="classified"),
        )

    asyncio.run(pause())
    store = FileRoomStore(tmp_path)
    store.put(room.code, room)
    restored = FileRoomStore(tmp_path).get(room.code)
    assert restored is not None and restored._pending_resolution is not None
    assert restored._pending_resolution.responses["p1"].value == "classified"
    assert "classified" not in json.dumps(restored.snapshot())

    ws = AsyncMock()
    restored.connections.connect("p1", ws)
    asyncio.run(restored.replay_pending_interaction("p1"))
    request = json.loads(ws.send_text.call_args.args[0])
    assert request["type"] == "interaction_request"
    assert request["progress"]["submitted"] is True
    assert "classified" not in json.dumps(request)


def test_pending_resolution_persists_turn_bookkeeping(tmp_path) -> None:
    plan = ResolutionPlan.model_validate(
        {
            "steps": [
                {
                    "kind": "interaction",
                    "result_key": "answer",
                    "request": {"kind": "confirm", "prompt": "Continue?", "audience": "all"},
                }
            ]
        }
    )
    room = _room_with_plan(plan)
    room.state = room.state.model_copy(update={"rules": room.state.rules.model_copy(update={"play": 2})})
    room._has_drawn = True
    room._plays_this_turn = 1
    room._deck_exhausted = True

    asyncio.run(room.handle_action("p1", PlayMsg(card_id="card")))
    store = FileRoomStore(tmp_path)
    store.put(room.code, room)
    restored = FileRoomStore(tmp_path).get(room.code)
    assert restored is not None
    assert restored._has_drawn is True
    assert restored._plays_this_turn == 1
    assert restored._deck_exhausted is True

    # Prove the restored play counter, rather than a reset counter, closes the
    # two-play turn after the pending play resolves.
    restored._deck_exhausted = False

    async def finish() -> None:
        pending = restored._pending_resolution
        assert pending is not None
        await restored.handle_action("p1", _response(pending.interaction_id, "confirm", confirmed=True))
        await restored.handle_action("p2", _response(pending.interaction_id, "confirm", confirmed=True))

    asyncio.run(finish())
    assert restored.state.turn_index == 1


def test_restored_timeout_runs_at_manager_start_without_reconnect(tmp_path) -> None:
    plan = ResolutionPlan.model_validate(
        {
            "steps": [
                {
                    "kind": "interaction",
                    "result_key": "answer",
                    "request": {"kind": "confirm", "prompt": "Continue?", "audience": "all"},
                }
            ]
        }
    )
    room = _room_with_plan(plan)
    asyncio.run(room.handle_action("p1", PlayMsg(card_id="card")))
    room._pending_resolution.deadline_at = datetime.now(UTC) - timedelta(seconds=1)
    store = FileRoomStore(tmp_path)
    store.put(room.code, room)

    async def restart() -> Room:
        cold_store = FileRoomStore(tmp_path)
        restored = cold_store.get(room.code)
        assert restored is not None
        manager = RoomManager(store=cold_store)
        manager.start_background_tasks()
        for _ in range(20):
            if restored._pending_resolution is None:
                break
            await asyncio.sleep(0.01)
        return restored

    restored = asyncio.run(restart())
    assert restored._pending_resolution is None
    assert restored.state.discard == ["card"]
    assert restored.state.cards["card"]["mechanical_status"] == "fallback"


def _resume_failure_plan() -> ResolutionPlan:
    """A number interaction (bids) whose follow-up snippet crashes on resume."""
    return ResolutionPlan.model_validate(
        {
            "steps": [
                {
                    "kind": "interaction",
                    "result_key": "bids",
                    "request": {"kind": "number", "prompt": "Bid", "audience": "all", "minimum": 0},
                },
                {"kind": "snippet", "code": "def apply(state, ctx):\n    raise RuntimeError('boom on resume')\n"},
            ]
        }
    )


def test_resume_failure_reports_triage_and_never_leaks_to_player_log(monkeypatch) -> None:
    monkeypatch.setenv("TRIAGE_AGENT_ENABLED", "true")
    get_settings.cache_clear()
    spy = MagicMock()
    monkeypatch.setattr(triage_module, "schedule_triage", spy)

    room = _room_with_plan(_resume_failure_plan())

    async def scenario() -> None:
        await room.handle_action("p1", PlayMsg(card_id="card"))
        interaction_id = room._pending_resolution.interaction_id
        await room.handle_action("p1", _response(interaction_id, "number", value=3))
        await room.handle_action("p2", _response(interaction_id, "number", value=5))

    asyncio.run(scenario())

    # The resume-path failure is now visible to triage...
    spy.assert_called_once()
    assert spy.call_args.args[0].kind == "interaction_resolve"
    # ...but its raw mechanical detail never reaches the shared player log.
    assert not any("[interaction] Op" in line for line in room.state.log)
    assert not any("boom on resume" in line for line in room.state.log)
    assert not any("failed validation" in line for line in room.state.log)
    # The card still resolves cleanly into the fallback.
    assert room._pending_resolution is None
    assert room.state.cards["card"]["mechanical_status"] == "fallback"


# The original Auction bug: an agent snippet minted the won card with
# ``create_card(destination='id:<winner>')`` — an unsupported destination that
# failed op validation on resume. per-player routing + the sandbox coercion make
# the exact scenario work end-to-end.
_AUCTION_SNIPPET = (
    "def apply(state, ctx):\n"
    "    bids = ctx['interactions']['bids']\n"
    "    winner_id = max(bids, key=lambda pid: bids[pid])\n"
    "    for pid, bid in bids.items():\n"
    "        if bid > 0:\n"
    "            state.subtract_points(f'id:{pid}', int(bid))\n"
    "    state.create_card(title='Double Cat', description='A dubious asset.', "
    "destination=f'id:{winner_id}', count=1)\n"
)


def test_auction_delivers_created_card_to_the_winner() -> None:
    plan = ResolutionPlan.model_validate(
        {
            "steps": [
                {
                    "kind": "interaction",
                    "result_key": "bids",
                    "request": {"kind": "number", "prompt": "Bid for the Double Cat", "audience": "all", "minimum": 0},
                },
                {"kind": "snippet", "code": _AUCTION_SNIPPET},
            ]
        }
    )
    room = _room_with_plan(plan)
    room.state = room.state.model_copy(
        update={"players": [player.model_copy(update={"score": 10}) for player in room.state.players]}
    )

    async def scenario() -> None:
        await room.handle_action("p1", PlayMsg(card_id="card"))
        interaction_id = room._pending_resolution.interaction_id
        await room.handle_action("p1", _response(interaction_id, "number", value=3))
        await room.handle_action("p2", _response(interaction_id, "number", value=5))

    asyncio.run(scenario())

    # p2 outbid p1 and receives the minted card; both paid their bids.
    assert room.state.get_player("p1").score == 7
    assert room.state.get_player("p2").score == 5
    created = [cid for cid in room.state.get_player("p2").hand if cid.startswith("created-")]
    assert created and room.state.cards[created[0]]["title"] == "Double Cat"
    # No validation error, no fallback: the card resolved for real.
    assert room.state.cards["card"]["mechanical_status"] == "applied"
    assert not any("failed validation" in line for line in room.state.log)
