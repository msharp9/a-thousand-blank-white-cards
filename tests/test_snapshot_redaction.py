"""Per-viewer snapshot redaction: hands and deck order never leave the server.

Covers the pure helper (board.rooms.redaction.redact_snapshot), the Room
facade (snapshot_for), the redacted REST state endpoint, and the WS-level
guarantee that two connected clients receive DIFFERENT snapshots — face-down
rendering on the client is a convention, not the secrecy mechanism.
"""

from __future__ import annotations

import asyncio
import copy

import pytest
from fastapi.testclient import TestClient

from board.app import create_app
from board.rooms.redaction import redact_snapshot
from board.rooms.room import Room


def _card(cid: str) -> dict:
    return {"id": cid, "title": f"Title {cid}", "description": f"Secret text of {cid}"}


def _snapshot(phase: str = "playing") -> dict:
    all_ids = ["a1", "a2", "b1", "b2", "b3", "d1", "d2", "d3", "d4", "x1", "hr1", "ip1"]
    return {
        "room_code": "ABCDEF",
        "phase": phase,
        "players": [
            {"id": "p1", "name": "Alice", "hand": ["a1", "a2"], "in_play": ["ip1"]},
            {"id": "p2", "name": "Bob", "hand": ["b1", "b2", "b3"], "in_play": []},
        ],
        "deck": ["d1", "d2", "d3", "d4"],
        "discard": ["x1"],
        "house_rules": ["hr1"],
        "cards": {cid: _card(cid) for cid in all_ids},
    }


def test_viewer_keeps_own_hand_and_sees_counts_for_others() -> None:
    view = redact_snapshot(_snapshot(), "p1")
    p1, p2 = view["players"]
    assert p1["hand"] == ["a1", "a2"]
    assert p1["hand_count"] == 2
    assert p2["hand"] == []
    assert p2["hand_count"] == 3


def test_deck_hidden_with_count_during_play() -> None:
    view = redact_snapshot(_snapshot(), "p1")
    assert view["deck"] == []
    assert view["deck_count"] == 4


def test_deck_stays_visible_during_lobby_and_setup() -> None:
    for phase in ("lobby", "setup"):
        view = redact_snapshot(_snapshot(phase), "p1")
        assert view["deck"] == ["d1", "d2", "d3", "d4"]
        assert view["deck_count"] == 4


def test_spectator_view_hides_every_hand() -> None:
    for viewer in (None, "spec-1"):
        view = redact_snapshot(_snapshot(), viewer)
        assert all(p["hand"] == [] for p in view["players"])
        assert [p["hand_count"] for p in view["players"]] == [2, 3]
        assert view["deck"] == []
        assert view["deck_count"] == 4


def test_public_zones_untouched() -> None:
    view = redact_snapshot(_snapshot(), "p2")
    assert view["discard"] == ["x1"]
    assert view["house_rules"] == ["hr1"]
    assert view["players"][0]["in_play"] == ["ip1"]


def test_cards_registry_drops_opponent_hand_and_deck_content() -> None:
    view = redact_snapshot(_snapshot(), "p1")
    assert set(view["cards"]) == {"a1", "a2", "ip1", "x1", "hr1"}


def test_cards_registry_keeps_deck_content_during_lobby_and_setup() -> None:
    for phase in ("lobby", "setup"):
        view = redact_snapshot(_snapshot(phase), "p1")
        assert {"d1", "d2", "d3", "d4"} <= set(view["cards"])
        assert "b1" not in view["cards"]


def test_cards_registry_spectator_sees_only_public_zones() -> None:
    for viewer in (None, "spec-1"):
        view = redact_snapshot(_snapshot(), viewer)
        assert set(view["cards"]) == {"ip1", "x1", "hr1"}


def test_cards_registry_keeps_revealed_content() -> None:
    snap = _snapshot()
    snap["pending_play"] = {"window_id": "w1", "card_id": "b1", "actor_id": "p2", "deadline_epoch_ms": 0}
    snap["history_events"] = [
        {"sequence": 1, "kind": "play", "card_id": "b2"},
        {"sequence": 2, "kind": "score", "card_id": None},
    ]
    snap["epilogue_result"] = {
        "kept": [{"id": "a1", "title": "Title a1"}],
        "destroyed": [{"id": "d1", "title": "Title d1"}],
    }
    view = redact_snapshot(snap, None)
    assert {"b1", "b2", "a1", "d1"} <= set(view["cards"])
    assert "b3" not in view["cards"]
    assert "d2" not in view["cards"]


def test_input_snapshot_is_not_mutated() -> None:
    snap = _snapshot()
    original = copy.deepcopy(snap)
    redact_snapshot(snap, "p1")
    redact_snapshot(snap, None)
    assert snap == original


def _playing_room(code: str = "REDACT") -> Room:
    room = Room(code)
    room.add_player("p1", "Alice")
    room.add_player("p2", "Bob")
    hands = {"p1": ["a1"], "p2": ["b1", "b2"]}
    players = [p.model_copy(update={"hand": hands[p.id]}) for p in room.state.players]
    cards = {cid: _card(cid) for cid in ("a1", "b1", "b2", "d1", "d2")}
    room.state = room.state.model_copy(
        update={"phase": "playing", "players": players, "deck": ["d1", "d2"], "cards": cards}
    )
    return room


def test_room_snapshot_for_redacts_per_viewer() -> None:
    room = _playing_room()
    view = room.snapshot_for("p2")
    by_id = {p["id"]: p for p in view["players"]}
    assert by_id["p2"]["hand"] == ["b1", "b2"]
    assert by_id["p1"]["hand"] == []
    assert by_id["p1"]["hand_count"] == 1
    assert view["deck"] == []
    assert view["deck_count"] == 2
    assert set(view["cards"]) == {"b1", "b2"}
    assert room.state.get_player("p1").hand == ["a1"]
    assert set(room.state.cards) == {"a1", "b1", "b2", "d1", "d2"}


def test_broadcast_state_sends_different_snapshots_per_connection() -> None:
    from unittest.mock import AsyncMock
    import json

    room = _playing_room()
    ws1, ws2 = AsyncMock(), AsyncMock()
    room.connections.connect("p1", ws1)
    room.connections.connect("p2", ws2)
    asyncio.run(room._broadcast_state())

    state1 = json.loads(ws1.send_text.call_args.args[0])["state"]
    state2 = json.loads(ws2.send_text.call_args.args[0])["state"]
    hands1 = {p["id"]: p["hand"] for p in state1["players"]}
    hands2 = {p["id"]: p["hand"] for p in state2["players"]}
    assert hands1 == {"p1": ["a1"], "p2": []}
    assert hands2 == {"p1": [], "p2": ["b1", "b2"]}
    assert state1["deck"] == [] and state2["deck"] == []
    assert state1["deck_count"] == state2["deck_count"] == 2


@pytest.fixture
def client() -> TestClient:
    return TestClient(create_app())


def _seat_two_players(client: TestClient) -> tuple[str, str, str]:
    code = client.post("/rooms").json()["code"]
    pid1 = client.post(f"/rooms/{code}/join", json={"name": "Alice"}).json()["player_id"]
    pid2 = client.post(f"/rooms/{code}/join", json={"name": "Bob"}).json()["player_id"]

    from board.rooms.manager import room_manager

    room = room_manager.get(code)
    hands = {pid1: ["a1", "a2"], pid2: ["b1"]}
    players = [p.model_copy(update={"hand": hands[p.id]}) for p in room.state.players]
    cards = {cid: _card(cid) for cid in ("a1", "a2", "b1", "d1", "d2", "d3")}
    room.state = room.state.model_copy(
        update={"phase": "playing", "players": players, "deck": ["d1", "d2", "d3"], "cards": cards}
    )
    return code, pid1, pid2


def test_ws_two_clients_receive_different_snapshots(client: TestClient) -> None:
    code, pid1, pid2 = _seat_two_players(client)

    with client.websocket_connect(f"/ws/{code}") as ws1:
        ws1.send_json({"type": "join", "player_id": pid1, "name": "Alice"})
        assert ws1.receive_json()["type"] == "state"

        with client.websocket_connect(f"/ws/{code}") as ws2:
            ws2.send_json({"type": "join", "player_id": pid2, "name": "Bob"})
            state2 = ws2.receive_json()["state"]
            # ws2's join re-broadcasts state; ws1's copy is Alice's view.
            state1 = ws1.receive_json()["state"]

    hands1 = {p["id"]: p["hand"] for p in state1["players"]}
    hands2 = {p["id"]: p["hand"] for p in state2["players"]}
    assert hands1 == {pid1: ["a1", "a2"], pid2: []}
    assert hands2 == {pid1: [], pid2: ["b1"]}
    counts = {p["id"]: p["hand_count"] for p in state1["players"]}
    assert counts == {pid1: 2, pid2: 1}
    assert state1["deck"] == [] and state2["deck"] == []
    assert state1["deck_count"] == state2["deck_count"] == 3
    assert set(state1["cards"]) == {"a1", "a2"}
    assert set(state2["cards"]) == {"b1"}


def test_ws_join_broadcast_snapshots_once_for_all_connections(client: TestClient) -> None:
    """One broadcast round = one state instant: every recipient's view must be a
    redaction of the SAME snapshot, or a task mutating state between the
    per-connection sends would give clients divergent game facts."""
    code, pid1, pid2 = _seat_two_players(client)

    from board.rooms.manager import room_manager

    room = room_manager.get(code)
    with client.websocket_connect(f"/ws/{code}") as ws1:
        ws1.send_json({"type": "join", "player_id": pid1, "name": "Alice"})
        assert ws1.receive_json()["type"] == "state"

        calls = 0
        original = room.snapshot

        def counting_snapshot() -> dict:
            nonlocal calls
            calls += 1
            return original()

        room.snapshot = counting_snapshot
        try:
            with client.websocket_connect(f"/ws/{code}") as ws2:
                ws2.send_json({"type": "join", "player_id": pid2, "name": "Bob"})
                assert ws2.receive_json()["type"] == "state"
                assert ws1.receive_json()["type"] == "state"
        finally:
            room.snapshot = original
    assert calls == 1


def test_rest_state_endpoint_serves_fully_hidden_view(client: TestClient) -> None:
    code, pid1, pid2 = _seat_two_players(client)
    data = client.get(f"/rooms/{code}/state").json()
    assert all(p["hand"] == [] for p in data["players"])
    assert {p["id"]: p["hand_count"] for p in data["players"]} == {pid1: 2, pid2: 1}
    assert data["deck"] == []
    assert data["deck_count"] == 3
    assert data["cards"] == {}
