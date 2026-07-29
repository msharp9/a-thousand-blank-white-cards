"""reveal_hand op (bead 7hd.2): reducer behavior, redactor honoring revealed
state per-viewer, one-shot push audience, and history privacy (player ids
only — never card ids)."""

from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock

from engine.compile import compile_card
from engine.events import GameEvent, HookContext
from engine.reducers import apply_op, collect_hand_reveals
from engine.sandbox.revalidate import parse_diff
from board.rooms.redaction import redact_snapshot
from board.rooms.room import Room
from models.effects import DestroyCardOp, MoveCardsOp, OpsStep, ResolutionPlan, RevealHandOp, op_requires_choice
from models.game_state import GameState, Player


def _card(cid: str) -> dict:
    return {"id": cid, "title": f"Title {cid}", "description": f"Secret text of {cid}"}


def _state() -> GameState:
    return GameState(
        room_code="REVEAL",
        phase="playing",
        players=[
            Player(id="p1", name="Alice", hand=["a1", "a2"]),
            Player(id="p2", name="Bob", hand=["b1"]),
            Player(id="p3", name="Cara", hand=["c1"]),
        ],
        cards={cid: _card(cid) for cid in ("a1", "a2", "b1", "c1")},
    )


def _ctx(actor: str = "p1") -> HookContext:
    return HookContext(event=GameEvent.ON_PLAY, actor_id=actor, card_id="played")


# ---------------------------------------------------------------------------
# Op model
# ---------------------------------------------------------------------------


def test_reveal_hand_op_defaults() -> None:
    op = RevealHandOp()
    assert (op.target, op.to, op.persistent, op.mode) == ("self", "all", False, "reveal")


def test_reveal_hand_to_chooser_requires_choice() -> None:
    assert op_requires_choice(RevealHandOp(to="chooser"))
    assert not op_requires_choice(RevealHandOp())


def test_compile_card_maps_reveal_hand_authoring_op() -> None:
    program = compile_card(
        {"ops": [{"op": "reveal_hand", "args": {"target": "self", "to": "next_player", "persistent": True}}]}
    )
    assert program is not None
    op = program.ops[0]
    assert isinstance(op, RevealHandOp)
    assert op.to == "left_neighbor"
    assert op.persistent is True


def test_parse_diff_accepts_reveal_hand_from_snippets() -> None:
    program = parse_diff([{"op": "reveal_hand", "target": "self", "to": "all", "persistent": True}])
    assert isinstance(program.ops[0], RevealHandOp)


# ---------------------------------------------------------------------------
# Reducer: persistent reveal / conceal
# ---------------------------------------------------------------------------


def test_persistent_reveal_to_all_sets_hand_public() -> None:
    state = apply_op(_state(), RevealHandOp(target="self", to="all", persistent=True), _ctx())
    assert state.get_player("p1").hand_public is True
    assert state.get_player("p1").hand_revealed_to == []
    assert state.get_player("p2").hand_public is False


def test_persistent_reveal_to_one_appends_viewer() -> None:
    state = apply_op(_state(), RevealHandOp(target="self", to="id:p2", persistent=True), _ctx())
    player = state.get_player("p1")
    assert player.hand_public is False
    assert player.hand_revealed_to == ["p2"]


def test_persistent_reveal_dedupes_and_never_adds_owner() -> None:
    state = apply_op(_state(), RevealHandOp(target="self", to="all_others", persistent=True), _ctx())
    state = apply_op(state, RevealHandOp(target="self", to="all_others", persistent=True), _ctx())
    assert state.get_player("p1").hand_revealed_to == ["p2", "p3"]


def test_persistent_reveal_target_all_reveals_every_hand() -> None:
    state = apply_op(_state(), RevealHandOp(target="all", to="all", persistent=True), _ctx())
    assert all(p.hand_public for p in state.players)


def test_conceal_all_clears_both_fields() -> None:
    state = apply_op(_state(), RevealHandOp(target="self", to="all", persistent=True), _ctx())
    state = apply_op(state, RevealHandOp(target="self", to="id:p3", persistent=True), _ctx())
    state = apply_op(state, RevealHandOp(target="self", to="all", mode="conceal"), _ctx())
    player = state.get_player("p1")
    assert player.hand_public is False
    assert player.hand_revealed_to == []


def test_conceal_removes_only_named_viewers() -> None:
    state = apply_op(_state(), RevealHandOp(target="self", to="all_others", persistent=True), _ctx())
    state = apply_op(state, RevealHandOp(target="self", to="id:p2", mode="conceal"), _ctx())
    assert state.get_player("p1").hand_revealed_to == ["p3"]


def test_unresolvable_target_is_a_logged_no_op() -> None:
    before = _state()
    state = apply_op(before, RevealHandOp(target="id:ghost", to="all", persistent=True), _ctx())
    assert [p.hand_public for p in state.players] == [False, False, False]
    assert "[reveal_hand no-op]" in state.log[-1]
    assert not state.history_events


# ---------------------------------------------------------------------------
# Reducer: one-shot reveal
# ---------------------------------------------------------------------------


def test_one_shot_reveal_changes_no_state_and_fills_the_drain() -> None:
    with collect_hand_reveals() as reveals:
        state = apply_op(_state(), RevealHandOp(target="self", to="id:p2"), _ctx())
    player = state.get_player("p1")
    assert player.hand_public is False and player.hand_revealed_to == []
    assert reveals == [
        {
            "player_id": "p1",
            "viewer_ids": ["p2"],
            "card_ids": ["a1", "a2"],
            "cards": {"a1": _card("a1"), "a2": _card("a2")},
        }
    ]


def test_one_shot_reveal_to_all_excludes_the_owner_from_the_audience() -> None:
    with collect_hand_reveals() as reveals:
        apply_op(_state(), RevealHandOp(target="self", to="all"), _ctx())
    assert reveals[0]["viewer_ids"] == ["p2", "p3"]


def test_one_shot_reveal_without_a_drain_does_not_crash() -> None:
    state = apply_op(_state(), RevealHandOp(target="self", to="all"), _ctx())
    assert state.history_events[-1].kind == "reveal"


# ---------------------------------------------------------------------------
# Persistent reveal binds to its board source card (bead 100.2)
# ---------------------------------------------------------------------------


def _board_state() -> GameState:
    state = _state()
    players = [p.model_copy(update={"in_play": ["rvl", "rvl2"]}) if p.id == "p1" else p for p in state.players]
    cards = {**state.cards, "rvl": _card("rvl"), "rvl2": _card("rvl2")}
    return state.model_copy(update={"players": players, "cards": cards})


def _card_ctx(card_id: str, actor: str = "p1") -> HookContext:
    return HookContext(event=GameEvent.ON_PLAY, actor_id=actor, card_id=card_id)


def test_stealing_the_reveal_card_ends_a_public_reveal() -> None:
    state = apply_op(_board_state(), RevealHandOp(target="self", to="all", persistent=True), _card_ctx("rvl"))
    assert state.get_player("p1").hand_public is True
    assert [b.source_card_id for b in state.reveal_bindings] == ["rvl"]
    state = apply_op(
        state,
        MoveCardsOp(card_target="id:rvl", to_zone="hand", to_player="id:p2"),
        _card_ctx("thief", actor="p2"),
    )
    assert "rvl" in state.get_player("p2").hand
    assert state.get_player("p1").hand_public is False
    assert state.reveal_bindings == []


def test_stealing_the_reveal_card_removes_granted_viewers() -> None:
    state = apply_op(_board_state(), RevealHandOp(target="self", to="all_others", persistent=True), _card_ctx("rvl"))
    assert state.get_player("p1").hand_revealed_to == ["p2", "p3"]
    state = apply_op(
        state,
        MoveCardsOp(card_target="id:rvl", to_zone="hand", to_player="id:p2"),
        _card_ctx("thief", actor="p2"),
    )
    assert state.get_player("p1").hand_revealed_to == []
    assert state.reveal_bindings == []


def test_destroying_the_reveal_card_ends_the_reveal() -> None:
    state = apply_op(_board_state(), RevealHandOp(target="self", to="id:p2", persistent=True), _card_ctx("rvl"))
    state = apply_op(state, DestroyCardOp(card_id="rvl"), _ctx("p2"))
    assert state.get_player("p1").hand_revealed_to == []
    assert state.reveal_bindings == []


def test_persistent_reveal_without_board_source_records_no_binding() -> None:
    state = apply_op(_state(), RevealHandOp(target="self", to="all", persistent=True), _ctx())
    assert state.get_player("p1").hand_public is True
    assert state.reveal_bindings == []


def test_conceal_then_retirement_does_not_resurrect_the_reveal() -> None:
    state = apply_op(_board_state(), RevealHandOp(target="self", to="all", persistent=True), _card_ctx("rvl"))
    state = apply_op(state, RevealHandOp(target="self", to="all", mode="conceal"), _ctx())
    assert state.reveal_bindings == []
    state = apply_op(state, DestroyCardOp(card_id="rvl"), _ctx("p2"))
    assert state.get_player("p1").hand_public is False
    assert state.get_player("p1").hand_revealed_to == []


def test_conceal_beats_stacked_public_bindings() -> None:
    state = apply_op(_board_state(), RevealHandOp(target="self", to="all", persistent=True), _card_ctx("rvl"))
    state = apply_op(state, RevealHandOp(target="self", to="all", persistent=True), _card_ctx("rvl2"))
    state = apply_op(state, RevealHandOp(target="self", to="all", mode="conceal"), _ctx())
    state = apply_op(state, DestroyCardOp(card_id="rvl2"), _ctx("p2"))
    assert state.get_player("p1").hand_public is False


def test_buried_public_binding_splices_without_hiding_the_hand() -> None:
    state = apply_op(_board_state(), RevealHandOp(target="self", to="all", persistent=True), _card_ctx("rvl"))
    state = apply_op(state, RevealHandOp(target="self", to="all", persistent=True), _card_ctx("rvl2"))
    state = apply_op(state, DestroyCardOp(card_id="rvl"), _ctx("p2"))
    assert state.get_player("p1").hand_public is True
    state = apply_op(state, DestroyCardOp(card_id="rvl2"), _ctx("p2"))
    assert state.get_player("p1").hand_public is False
    assert state.reveal_bindings == []


def test_partial_conceal_drops_only_that_viewers_binding() -> None:
    state = apply_op(_board_state(), RevealHandOp(target="self", to="all_others", persistent=True), _card_ctx("rvl"))
    state = apply_op(state, RevealHandOp(target="self", to="id:p2", mode="conceal"), _ctx())
    assert [b.viewer_id for b in state.reveal_bindings] == ["p3"]
    state = apply_op(state, DestroyCardOp(card_id="rvl"), _ctx("p2"))
    assert state.get_player("p1").hand_revealed_to == []


def test_viewer_still_granted_by_a_live_binding_survives_release() -> None:
    state = apply_op(_board_state(), RevealHandOp(target="self", to="id:p2", persistent=True), _card_ctx("rvl"))
    state = apply_op(state, RevealHandOp(target="self", to="id:p2", persistent=True), _card_ctx("rvl2"))
    assert state.get_player("p1").hand_revealed_to == ["p2"]
    assert [b.source_card_id for b in state.reveal_bindings] == ["rvl", "rvl2"]
    state = apply_op(state, DestroyCardOp(card_id="rvl"), _ctx("p2"))
    assert state.get_player("p1").hand_revealed_to == ["p2"]
    assert [b.source_card_id for b in state.reveal_bindings] == ["rvl2"]


def test_reveal_bindings_survive_snapshot_round_trip() -> None:
    state = apply_op(_board_state(), RevealHandOp(target="self", to="all", persistent=True), _card_ctx("rvl"))
    state = apply_op(state, RevealHandOp(target="self", to="id:p2", persistent=True), _card_ctx("rvl2"))
    restored = GameState.model_validate(state.model_dump())
    assert restored.reveal_bindings == state.reveal_bindings
    after = apply_op(restored, DestroyCardOp(card_target="all_in_play"), _ctx("p2"))
    assert after.get_player("p1").hand_public is False
    assert after.get_player("p1").hand_revealed_to == []
    assert after.reveal_bindings == []


def test_redactor_strips_reveal_bindings_for_every_viewer() -> None:
    snap = _state().model_dump()
    snap["reveal_bindings"] = [
        {"source_card_id": "rvl", "player_id": "p1", "viewer_id": "p2", "previous_public": False}
    ]
    for viewer in ("p1", "p2", "p3", "spec-1", None):
        assert "reveal_bindings" not in redact_snapshot(snap, viewer)


# ---------------------------------------------------------------------------
# History privacy
# ---------------------------------------------------------------------------


def test_history_records_reveal_with_player_ids_only() -> None:
    state = apply_op(_state(), RevealHandOp(target="all_others", to="id:p1", persistent=True), _ctx())
    event = state.history_events[-1]
    assert event.kind == "reveal"
    assert event.actor_id == "p1"
    assert event.target_player_ids == ["p2", "p3"]
    assert event.card_id is None
    assert event.source == "reveal"


def test_history_records_conceal_mode() -> None:
    state = apply_op(_state(), RevealHandOp(target="self", to="all", mode="conceal"), _ctx())
    event = state.history_events[-1]
    assert event.kind == "reveal"
    assert event.source == "conceal"
    assert event.card_id is None


# ---------------------------------------------------------------------------
# Redactor honors revealed state per-viewer
# ---------------------------------------------------------------------------


def _snapshot(**p1_extra) -> dict:
    snap = _state().model_dump()
    if p1_extra:
        snap["players"][0].update(p1_extra)
    return snap


def test_redactor_serves_public_hand_to_everyone_including_spectators() -> None:
    snap = _snapshot(hand_public=True)
    for viewer in ("p2", "p3", "spec-1", None):
        view = redact_snapshot(snap, viewer)
        by_id = {p["id"]: p for p in view["players"]}
        assert by_id["p1"]["hand"] == ["a1", "a2"]
        assert {"a1", "a2"} <= set(view["cards"])
        assert by_id["p2"]["hand"] == ([] if viewer != "p2" else ["b1"])


def test_redactor_serves_revealed_hand_only_to_permitted_viewer() -> None:
    snap = _snapshot(hand_revealed_to=["p2"])
    view_p2 = redact_snapshot(snap, "p2")
    assert {p["id"]: p["hand"] for p in view_p2["players"]}["p1"] == ["a1", "a2"]
    assert {"a1", "a2"} <= set(view_p2["cards"])

    view_p3 = redact_snapshot(snap, "p3")
    assert {p["id"]: p["hand"] for p in view_p3["players"]}["p1"] == []
    assert "a1" not in view_p3["cards"] and "a2" not in view_p3["cards"]

    view_anon = redact_snapshot(snap, None)
    assert {p["id"]: p["hand"] for p in view_anon["players"]}["p1"] == []


def test_redactor_scrubs_reveal_audience_from_unauthorized_viewers() -> None:
    snap = _snapshot(hand_revealed_to=["p2"])
    for viewer in ("p3", "spec-1", None):
        view = redact_snapshot(snap, viewer)
        assert {p["id"]: p["hand_revealed_to"] for p in view["players"]}["p1"] == []


def test_redactor_keeps_full_reveal_audience_for_the_owner() -> None:
    snap = _snapshot(hand_revealed_to=["p2", "p3"])
    view = redact_snapshot(snap, "p1")
    assert {p["id"]: p["hand_revealed_to"] for p in view["players"]}["p1"] == ["p2", "p3"]


def test_redactor_shows_permitted_viewer_only_their_own_audience_membership() -> None:
    snap = _snapshot(hand_revealed_to=["p2", "p3"])
    view = redact_snapshot(snap, "p2")
    assert {p["id"]: p["hand_revealed_to"] for p in view["players"]}["p1"] == ["p2"]


def test_redactor_still_counts_revealed_hands() -> None:
    view = redact_snapshot(_snapshot(hand_public=True), "p2")
    counts = {p["id"]: p["hand_count"] for p in view["players"]}
    assert counts == {"p1": 2, "p2": 1, "p3": 1}


# ---------------------------------------------------------------------------
# One-shot push audience (Room-level)
# ---------------------------------------------------------------------------


def _reveal_room() -> tuple[Room, dict[str, AsyncMock]]:
    room = Room("REVEAL")
    room.add_player("p1", "Alice")
    room.add_player("p2", "Bob")
    room.add_player("p3", "Cara")
    hands = {"p1": ["a1", "a2", "played"], "p2": ["b1"], "p3": ["c1"]}
    players = [p.model_copy(update={"hand": hands[p.id]}) for p in room.state.players]
    cards = {cid: _card(cid) for cid in ("a1", "a2", "b1", "c1", "played")}
    room.state = room.state.model_copy(update={"phase": "playing", "players": players, "cards": cards})
    sockets = {}
    for pid in ("p1", "p2", "p3"):
        sockets[pid] = AsyncMock()
        room.connections.connect(pid, sockets[pid])
    return room, sockets


def _messages(ws: AsyncMock, mtype: str) -> list[dict]:
    return [msg for call in ws.send_text.call_args_list if (msg := json.loads(call.args[0])).get("type") == mtype]


def test_one_shot_push_goes_only_to_the_resolved_audience() -> None:
    room, sockets = _reveal_room()
    plan = ResolutionPlan(steps=[OpsStep(ops=[RevealHandOp(target="self", to="id:p2")])])
    ctx = HookContext(event=GameEvent.ON_PLAY, actor_id="p1", card_id="played")
    state = asyncio.run(room._execute_plan(room.state, plan, ctx, room.state.cards["played"]))

    assert _messages(sockets["p1"], "hand_revealed") == []
    assert _messages(sockets["p3"], "hand_revealed") == []
    pushed = _messages(sockets["p2"], "hand_revealed")
    assert len(pushed) == 1
    msg = pushed[0]
    assert msg["player_id"] == "p1"
    assert msg["player_name"] == "Alice"
    # The played card already left the hand; only the remaining cards reveal,
    # and their bodies ride the push (redacted snapshots can't resolve them).
    assert msg["card_ids"] == ["a1", "a2"]
    assert set(msg["cards"]) == {"a1", "a2"}
    assert msg["cards"]["a1"]["description"] == "Secret text of a1"

    # One-shot: nothing persisted — a reconnecting p2 sees a private hand again.
    assert state.get_player("p1").hand_public is False
    assert state.get_player("p1").hand_revealed_to == []


def test_one_shot_push_to_all_reaches_everyone_but_the_owner() -> None:
    room, sockets = _reveal_room()
    plan = ResolutionPlan(steps=[OpsStep(ops=[RevealHandOp(target="self", to="all")])])
    ctx = HookContext(event=GameEvent.ON_PLAY, actor_id="p1", card_id="played")
    asyncio.run(room._execute_plan(room.state, plan, ctx, room.state.cards["played"]))

    assert _messages(sockets["p1"], "hand_revealed") == []
    assert len(_messages(sockets["p2"], "hand_revealed")) == 1
    assert len(_messages(sockets["p3"], "hand_revealed")) == 1


def test_persistent_reveal_rides_the_snapshot_per_viewer() -> None:
    room, _ = _reveal_room()
    plan = ResolutionPlan(steps=[OpsStep(ops=[RevealHandOp(target="self", to="id:p2", persistent=True)])])
    ctx = HookContext(event=GameEvent.ON_PLAY, actor_id="p1", card_id="played")
    room.state = asyncio.run(room._execute_plan(room.state, plan, ctx, room.state.cards["played"]))

    view_p2 = room.snapshot_for("p2")
    assert {p["id"]: p["hand"] for p in view_p2["players"]}["p1"] == ["a1", "a2"]
    view_p3 = room.snapshot_for("p3")
    assert {p["id"]: p["hand"] for p in view_p3["players"]}["p1"] == []
