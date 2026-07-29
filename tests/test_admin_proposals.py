from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import pytest

from board.rooms.room import Room
from board.rooms.store import FileRoomStore
from models.admin import (
    EliminatePlayersAdminAction,
    EndGameAdminAction,
    MoveCardAdminAction,
    RemoveHookAdminAction,
    SetConditionAdminAction,
    SetResultWinnersAdminAction,
    SetScoreAdminAction,
    ShuffleDeckAdminAction,
)
from models.game_state import HookSpec
from models.ws_messages import AdminCancelMsg, AdminProposeMsg, AdminViewMsg, AdminVoteMsg


def _room(mode: str = "in_person", *, phase: str = "playing") -> Room:
    room = Room("ADMIN1", mode=mode)
    for player_id, name in (("p1", "Alice"), ("p2", "Bob"), ("p3", "Cara")):
        room.add_player(player_id, name)
        room.connections.connect(player_id, AsyncMock())
    cards = {
        "d1": {"id": "d1", "title": "Hidden one", "description": ""},
        "d2": {"id": "d2", "title": "Hidden two", "description": ""},
        "x": {"id": "x", "title": "Public card", "description": ""},
        "rule": {"id": "rule", "title": "Rule card", "description": ""},
    }
    room.state = room.state.model_copy(
        update={
            "phase": phase,
            "turn_number": 3,
            "deck": ["d1", "d2"],
            "discard": ["x"],
            "cards": cards,
        }
    )
    room._has_drawn = True
    return room


def _spectator_host_room() -> Room:
    room = _room()
    room.add_spectator("s1", "Morgan")
    room.connections.connect("s1", AsyncMock())
    players = [
        player.model_copy(update={"hand": ["h1"]}) if player.id == "p2" else player for player in room.state.players
    ]
    cards = {
        **room.state.cards,
        "h1": {"id": "h1", "title": "Secret Hand Card", "description": ""},
    }
    room.state = room.state.model_copy(update={"host_id": "s1", "players": players, "cards": cards})
    return room


@pytest.mark.parametrize("mode", ["online", "in_person", "both"])
def test_host_controls_are_available_in_every_room_mode(mode: str) -> None:
    room = _room(mode)
    asyncio.run(
        room.handle_action(
            "p1",
            AdminProposeMsg(actions=[SetScoreAdminAction(player_id="p2", score=7)]),
        )
    )
    assert room._pending_admin is not None


def test_proposal_changes_nothing_until_every_other_player_approves() -> None:
    room = _room()
    before = room.state.model_dump()
    propose = AdminProposeMsg(
        actions=[
            SetScoreAdminAction(player_id="p2", score=8),
            SetScoreAdminAction(player_id="p3", score=-2),
        ]
    )

    asyncio.run(room.handle_action("p1", propose))

    assert room.state.model_dump() == before
    proposal = room._pending_admin
    assert proposal is not None
    assert proposal.required_voter_ids == ["p2", "p3"]
    assert proposal.approvals == []

    asyncio.run(room.handle_action("p2", AdminVoteMsg(proposal_id=proposal.proposal_id, accept=True)))
    assert room.state.model_dump() == before
    assert room._pending_admin is not None

    asyncio.run(room.handle_action("p3", AdminVoteMsg(proposal_id=proposal.proposal_id, accept=True)))
    assert room._pending_admin is None
    assert room.state.get_player("p2").score == 8
    assert room.state.get_player("p3").score == -2
    audits = [event for event in room.state.history_events if event.kind == "admin_change"]
    assert len(audits) == 1
    assert audits[0].source == "applied"


def test_rejection_cancels_without_gameplay_change() -> None:
    room = _room()
    asyncio.run(
        room.handle_action(
            "p1",
            AdminProposeMsg(actions=[SetScoreAdminAction(player_id="p2", score=99)]),
        )
    )
    proposal_id = room._pending_admin.proposal_id

    asyncio.run(room.handle_action("p2", AdminVoteMsg(proposal_id=proposal_id, accept=False)))

    assert room._pending_admin is None
    assert room.state.get_player("p2").score == 0
    assert room.state.history_events[-1].kind == "admin_change"
    assert room.state.history_events[-1].source == "rejected"


def test_expired_proposal_cancels_without_gameplay_change() -> None:
    room = _room()
    asyncio.run(
        room.handle_action(
            "p1",
            AdminProposeMsg(actions=[SetScoreAdminAction(player_id="p2", score=99)]),
        )
    )
    proposal = room._pending_admin
    assert proposal is not None
    room._pending_admin = proposal.model_copy(update={"deadline_at": datetime.now(UTC) - timedelta(seconds=1)})

    asyncio.run(
        room.handle_action(
            "p2",
            AdminVoteMsg(proposal_id=proposal.proposal_id, accept=True),
        )
    )

    assert room._pending_admin is None
    assert room.state.get_player("p2").score == 0
    assert room.state.history_events[-1].source == "expired"


def test_host_can_cancel_but_non_host_cannot() -> None:
    room = _room()
    asyncio.run(
        room.handle_action(
            "p1",
            AdminProposeMsg(actions=[SetScoreAdminAction(player_id="p2", score=4)]),
        )
    )
    proposal_id = room._pending_admin.proposal_id

    asyncio.run(room.handle_action("p2", AdminCancelMsg(proposal_id=proposal_id)))
    assert room._pending_admin is not None

    asyncio.run(room.handle_action("p1", AdminCancelMsg(proposal_id=proposal_id)))
    assert room._pending_admin is None
    assert room.state.get_player("p2").score == 0


def test_non_host_and_spectator_cannot_propose() -> None:
    room = _room()
    room.add_spectator("s1", "Watcher")
    room.connections.connect("s1", AsyncMock())
    message = AdminProposeMsg(actions=[SetScoreAdminAction(player_id="p2", score=4)])

    asyncio.run(room.handle_action("p2", message))
    asyncio.run(room.handle_action("s1", message))

    assert room._pending_admin is None


def test_pending_snapshot_is_safe_and_never_contains_actions_or_deck_ids() -> None:
    room = _room()
    asyncio.run(
        room.handle_action(
            "p1",
            AdminProposeMsg(
                actions=[
                    MoveCardAdminAction(
                        source_zone="deck",
                        selector="top",
                        to_zone="center",
                    )
                ]
            ),
        )
    )

    snapshot = room.snapshot_for("p2")
    pending = snapshot["pending_admin_proposal"]
    public_description = str({"preview": pending["preview"], "warnings": pending["warnings"]})
    assert "actions" not in pending
    assert "d1" not in public_description
    assert "Hidden one" not in public_description
    assert pending["preview"][0]["detail"].startswith("Top card of deck")


def test_public_card_move_and_shuffle_apply_atomically() -> None:
    room = _room()
    actions = [
        MoveCardAdminAction(
            source_zone="discard",
            card_id="x",
            to_zone="deck",
            deck_position="bottom",
        ),
        ShuffleDeckAdminAction(),
    ]
    asyncio.run(room.handle_action("p1", AdminProposeMsg(actions=actions)))
    proposal_id = room._pending_admin.proposal_id
    asyncio.run(room.handle_action("p2", AdminVoteMsg(proposal_id=proposal_id, accept=True)))
    asyncio.run(room.handle_action("p3", AdminVoteMsg(proposal_id=proposal_id, accept=True)))

    assert "x" not in room.state.discard
    assert set(room.state.deck) == {"d1", "d2", "x"}
    assert room._deck_exhausted is False


def test_exact_hook_removal_does_not_remove_sibling_hook() -> None:
    room = _room()
    room.state = room.state.model_copy(
        update={
            "house_rules": ["rule"],
            "hooks": [
                HookSpec(
                    id="hook-rule-0",
                    source_card_id="rule",
                    event="on_turn_start",
                    code="def apply(state, ctx):\n    return None",
                ),
                HookSpec(
                    id="hook-rule-1",
                    source_card_id="rule",
                    event="on_turn_end",
                    code="def apply(state, ctx):\n    return None",
                ),
            ],
        }
    )
    asyncio.run(
        room.handle_action(
            "p1",
            AdminProposeMsg(actions=[RemoveHookAdminAction(hook_id="hook-rule-0")]),
        )
    )
    proposal_id = room._pending_admin.proposal_id
    asyncio.run(room.handle_action("p2", AdminVoteMsg(proposal_id=proposal_id, accept=True)))
    asyncio.run(room.handle_action("p3", AdminVoteMsg(proposal_id=proposal_id, accept=True)))

    assert [hook.id for hook in room.state.hooks] == ["hook-rule-1"]


def test_condition_preview_uses_a_table_facing_name() -> None:
    room = _room()
    asyncio.run(
        room.handle_action(
            "p1",
            AdminProposeMsg(
                actions=[
                    SetConditionAdminAction(
                        player_id="p2",
                        key="speak_only_in_questions",
                        value=True,
                    )
                ]
            ),
        )
    )

    proposal = room._pending_admin
    assert proposal is not None
    assert proposal.preview[0].detail == "Bob: speak only in questions"
    assert "speak_only_in_questions" not in proposal.preview[0].detail


def test_eliminating_active_player_rotates_to_next_player() -> None:
    room = _room()
    asyncio.run(
        room.handle_action(
            "p1",
            AdminProposeMsg(actions=[EliminatePlayersAdminAction(player_ids=["p1"])]),
        )
    )
    proposal_id = room._pending_admin.proposal_id
    asyncio.run(room.handle_action("p2", AdminVoteMsg(proposal_id=proposal_id, accept=True)))
    asyncio.run(room.handle_action("p3", AdminVoteMsg(proposal_id=proposal_id, accept=True)))

    assert room.state.get_player("p1").eliminated is True
    assert room.state.active_player().id == "p2"


def test_admin_end_game_uses_declared_winners_without_emitting_gameplay_hooks() -> None:
    room = _room()
    room._emit_hooks = AsyncMock()
    asyncio.run(
        room.handle_action(
            "p1",
            AdminProposeMsg(actions=[EndGameAdminAction(winner_ids=["p2"])]),
        )
    )
    proposal_id = room._pending_admin.proposal_id
    asyncio.run(
        room.handle_action(
            "p2",
            AdminVoteMsg(proposal_id=proposal_id, accept=True),
        )
    )
    asyncio.run(
        room.handle_action(
            "p3",
            AdminVoteMsg(proposal_id=proposal_id, accept=True),
        )
    )

    assert room.state.phase == "results"
    assert room.state.winner_ids == ["p2"]
    room._emit_hooks.assert_not_awaited()


def test_results_correction_sets_scores_and_explicit_winners() -> None:
    room = _room(phase="results")
    room.state = room.state.model_copy(update={"winner_ids": ["p1"]})
    actions = [
        SetScoreAdminAction(player_id="p2", score=12),
        SetResultWinnersAdminAction(winner_ids=["p2"]),
    ]
    asyncio.run(room.handle_action("p1", AdminProposeMsg(actions=actions)))
    proposal_id = room._pending_admin.proposal_id
    asyncio.run(room.handle_action("p2", AdminVoteMsg(proposal_id=proposal_id, accept=True)))
    asyncio.run(room.handle_action("p3", AdminVoteMsg(proposal_id=proposal_id, accept=True)))

    assert room.state.phase == "results"
    assert room.state.get_player("p2").score == 12
    assert room.state.winner_ids == ["p2"]


def test_pending_proposal_round_trips_through_file_store(tmp_path) -> None:
    room = _room()
    asyncio.run(
        room.handle_action(
            "p1",
            AdminProposeMsg(actions=[SetScoreAdminAction(player_id="p2", score=5)]),
        )
    )
    store = FileRoomStore(tmp_path)
    store.put(room.code, room)

    restored = FileRoomStore(tmp_path).get(room.code)

    assert restored is not None
    assert restored._pending_admin is not None
    assert restored._pending_admin.model_dump() == room._pending_admin.model_dump()


def test_spectator_host_proposal_requires_every_player() -> None:
    room = _spectator_host_room()

    asyncio.run(
        room.handle_action(
            "s1",
            AdminProposeMsg(actions=[SetScoreAdminAction(player_id="p2", score=7)]),
        )
    )

    assert room._pending_admin is not None
    assert room._pending_admin.required_voter_ids == ["p1", "p2", "p3"]
    assert room.state.get_player("p2").score == 0
    for player_id in ("p1", "p2"):
        asyncio.run(
            room.handle_action(
                player_id,
                AdminVoteMsg(
                    proposal_id=room._pending_admin.proposal_id,
                    accept=True,
                ),
            )
        )
        assert room.state.get_player("p2").score == 0
    asyncio.run(
        room.handle_action(
            "p3",
            AdminVoteMsg(
                proposal_id=room._pending_admin.proposal_id,
                accept=True,
            ),
        )
    )
    assert room.state.get_player("p2").score == 7


def test_hidden_hand_move_preview_is_personalized_and_audit_is_generic() -> None:
    room = _spectator_host_room()
    action = MoveCardAdminAction(
        source_zone="hand",
        source_player_id="p2",
        card_id="h1",
        to_zone="discard",
    )

    asyncio.run(room.handle_action("s1", AdminProposeMsg(actions=[action])))

    host_item = room.snapshot_for("s1")["pending_admin_proposal"]["preview"][0]
    owner_item = room.snapshot_for("p2")["pending_admin_proposal"]["preview"][0]
    other_item = room.snapshot_for("p3")["pending_admin_proposal"]["preview"][0]
    assert "Secret Hand Card" in host_item["detail"]
    assert "Secret Hand Card" in owner_item["detail"]
    assert "Secret Hand Card" not in other_item["detail"]
    assert "selected hidden card" in other_item["detail"].lower()
    assert "private_detail" not in host_item
    assert "private_viewer_ids" not in host_item

    proposal_id = room._pending_admin.proposal_id
    asyncio.run(room.handle_action("p1", AdminVoteMsg(proposal_id=proposal_id, accept=False)))
    audit = room.state.history_events[-1]
    assert audit.kind == "admin_change"
    assert "Secret Hand Card" not in str(audit.data)
    assert "h1" not in str(audit.data)


def test_spectator_host_can_move_hand_card_to_random_deck_position() -> None:
    room = _spectator_host_room()
    action = MoveCardAdminAction(
        source_zone="hand",
        source_player_id="p2",
        card_id="h1",
        to_zone="deck",
        deck_position="shuffle",
    )

    asyncio.run(room.handle_action("s1", AdminProposeMsg(actions=[action])))
    proposal_id = room._pending_admin.proposal_id
    for player_id in ("p1", "p2", "p3"):
        asyncio.run(
            room.handle_action(
                player_id,
                AdminVoteMsg(proposal_id=proposal_id, accept=True),
            )
        )

    assert "h1" not in room.state.get_player("p2").hand
    assert "h1" in room.state.deck


def test_player_host_cannot_probe_exact_hidden_card_ids() -> None:
    room = _room()
    socket = room.connections.get("p1")
    socket.reset_mock()
    action = MoveCardAdminAction(
        source_zone="deck",
        card_id="d1",
        to_zone="discard",
    )

    asyncio.run(room.handle_action("p1", AdminProposeMsg(actions=[action])))

    assert room._pending_admin is None
    payload = socket.send_text.call_args.args[0]
    assert "spectator host" in payload
    assert "d1" not in payload


def test_admin_view_is_full_card_state_but_normal_spectator_state_stays_hidden() -> None:
    room = _spectator_host_room()
    socket = room.connections.get("s1")
    socket.reset_mock()

    normal = room.snapshot_for("s1")
    assert all(not player["hand"] for player in normal["players"])
    assert normal["deck"] == []
    assert "h1" not in normal["cards"]

    asyncio.run(room.handle_action("s1", AdminViewMsg(open=True)))
    payload = json.loads(socket.send_text.call_args.args[0])
    assert payload["type"] == "admin_state"
    admin = payload["state"]
    assert next(player for player in admin["players"] if player["id"] == "p2")["hand"] == ["h1"]
    assert admin["deck"] == ["d1", "d2"]
    assert {"h1", "d1", "d2"} <= set(admin["cards"])

    asyncio.run(room.handle_action("s1", AdminViewMsg(open=False)))
    assert "s1" not in room._admin_viewers
