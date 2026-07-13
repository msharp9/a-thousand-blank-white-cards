"""Tests for the /rooms REST routes."""

from __future__ import annotations

import asyncio

import pytest
from fastapi.testclient import TestClient

from config import get_settings
from board.app import create_app
from board.rooms.room import CARDS_TO_AUTHOR, STARTING_HAND_SIZE, Room


@pytest.fixture
def client() -> TestClient:
    return TestClient(create_app())


@pytest.fixture
def dev_client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setenv("DEV_MODE", "true")
    get_settings.cache_clear()
    return TestClient(create_app())


def test_create_room(client: TestClient) -> None:
    resp = client.post("/rooms")
    assert resp.status_code == 200
    assert len(resp.json()["code"]) == 6


def test_join_room(client: TestClient) -> None:
    code = client.post("/rooms").json()["code"]
    resp = client.post(f"/rooms/{code}/join", json={"name": "Alice"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["code"] == code
    assert data["player_id"]


def test_join_missing_room(client: TestClient) -> None:
    resp = client.post("/rooms/ZZZZZZ/join", json={"name": "Bob"})
    assert resp.status_code == 404


def test_join_room_registers_player(client: TestClient) -> None:
    code = client.post("/rooms").json()["code"]
    pid = client.post(f"/rooms/{code}/join", json={"name": "Alice"}).json()["player_id"]
    # the same player_id should now be in that room
    from board.rooms.manager import room_manager

    room = room_manager.get(code)
    assert room is not None
    assert any(p.id == pid for p in room.state.players)


def test_list_rooms_returns_created_room_with_fields(client: TestClient) -> None:
    code = client.post("/rooms", json={"mode": "in_person"}).json()["code"]
    client.post(f"/rooms/{code}/join", json={"name": "Alice"})

    resp = client.get("/rooms")
    assert resp.status_code == 200
    rooms = {r["code"]: r for r in resp.json()["rooms"]}
    assert code in rooms
    entry = rooms[code]
    assert entry["phase"] == "lobby"
    assert entry["mode"] == "in_person"
    assert entry["player_count"] == 1
    assert entry["spectator_count"] == 0
    assert entry["joinable"] is True
    # ISO-parseable timestamp.
    from datetime import datetime

    datetime.fromisoformat(entry["created_at"])


def test_list_rooms_default_excludes_started_game_but_all_true_includes_it(dev_client: TestClient) -> None:
    code = dev_client.post("/rooms").json()["code"]
    dev_client.post(f"/rooms/{code}/join", json={"name": "Alice"})
    dev_client.post(f"/rooms/{code}/join", json={"name": "Bob"})

    default_codes = {r["code"] for r in dev_client.get("/rooms").json()["rooms"]}
    assert code in default_codes

    dev_client.post(f"/rooms/{code}/dev/skip-setup")

    default_codes = {r["code"] for r in dev_client.get("/rooms").json()["rooms"]}
    assert code not in default_codes

    all_rooms = {r["code"]: r for r in dev_client.get("/rooms", params={"all": "true"}).json()["rooms"]}
    assert code in all_rooms
    assert all_rooms[code]["phase"] == "playing"
    assert all_rooms[code]["joinable"] is False


def test_list_rooms_excludes_ended_even_with_all(client: TestClient) -> None:
    code = client.post("/rooms").json()["code"]
    from board.rooms.manager import room_manager

    room = room_manager.get(code)
    room.state = room.state.model_copy(update={"phase": "ended"})

    resp = client.get("/rooms", params={"all": "true"})
    codes = {r["code"] for r in resp.json()["rooms"]}
    assert code not in codes


def test_list_rooms_sorted_newest_first(client: TestClient) -> None:
    from datetime import UTC, datetime, timedelta

    from board.rooms.manager import room_manager

    older = client.post("/rooms").json()["code"]
    newer = client.post("/rooms").json()["code"]
    now = datetime.now(UTC)
    room_manager.get(older).created_at = now - timedelta(hours=1)
    room_manager.get(newer).created_at = now

    codes = [r["code"] for r in client.get("/rooms").json()["rooms"]]
    assert codes.index(newer) < codes.index(older)


def test_get_room_state(client: TestClient) -> None:
    code = client.post("/rooms").json()["code"]
    client.post(f"/rooms/{code}/join", json={"name": "Alice"})
    resp = client.get(f"/rooms/{code}/state")
    assert resp.status_code == 200
    data = resp.json()
    assert isinstance(data, dict)
    assert data["room_code"] == code
    assert data["phase"] == "lobby"
    assert any(p["name"] == "Alice" for p in data["players"])


def test_get_room_state_missing_room(client: TestClient) -> None:
    resp = client.get("/rooms/ZZZZZZ/state")
    assert resp.status_code == 404


def test_dev_skip_setup_fast_forwards_to_playing(dev_client: TestClient) -> None:
    code = dev_client.post("/rooms").json()["code"]
    dev_client.post(f"/rooms/{code}/join", json={"name": "Alice"})
    dev_client.post(f"/rooms/{code}/join", json={"name": "Bob"})

    resp = dev_client.post(f"/rooms/{code}/dev/skip-setup")
    assert resp.status_code == 200
    data = resp.json()
    assert data["phase"] == "playing"
    assert data["players"]
    # The first player's turn began with the automatic draw on top of the deal.
    draw_count = data["draw_count"]
    active_id = data["players"][data["turn_index"]]["id"]
    for p in data["players"]:
        expected = STARTING_HAND_SIZE + (draw_count if p["id"] == active_id else 0)
        assert len(p["hand"]) == expected
    assert data["deck"]


def test_dev_skip_setup_hidden_when_dev_mode_off(client: TestClient) -> None:
    code = client.post("/rooms").json()["code"]
    client.post(f"/rooms/{code}/join", json={"name": "Alice"})
    resp = client.post(f"/rooms/{code}/dev/skip-setup")
    assert resp.status_code == 404


def test_dev_skip_setup_on_playing_room_returns_409(dev_client: TestClient) -> None:
    code = dev_client.post("/rooms").json()["code"]
    dev_client.post(f"/rooms/{code}/join", json={"name": "Alice"})
    dev_client.post(f"/rooms/{code}/join", json={"name": "Bob"})
    assert dev_client.post(f"/rooms/{code}/dev/skip-setup").status_code == 200

    resp = dev_client.post(f"/rooms/{code}/dev/skip-setup")
    assert resp.status_code == 409


def test_dev_end_game_opens_results(dev_client: TestClient) -> None:
    code = dev_client.post("/rooms").json()["code"]
    dev_client.post(f"/rooms/{code}/join", json={"name": "Alice"})
    dev_client.post(f"/rooms/{code}/join", json={"name": "Bob"})
    assert dev_client.post(f"/rooms/{code}/dev/skip-setup").status_code == 200

    resp = dev_client.post(f"/rooms/{code}/dev/end-game")
    assert resp.status_code == 200
    data = resp.json()
    assert data["phase"] in ("results", "ended")
    assert "winner_ids" in data


def test_dev_end_game_on_lobby_returns_409(dev_client: TestClient) -> None:
    code = dev_client.post("/rooms").json()["code"]
    dev_client.post(f"/rooms/{code}/join", json={"name": "Alice"})
    resp = dev_client.post(f"/rooms/{code}/dev/end-game")
    assert resp.status_code == 409


def test_dev_end_game_hidden_when_dev_mode_off(client: TestClient) -> None:
    code = client.post("/rooms").json()["code"]
    client.post(f"/rooms/{code}/join", json={"name": "Alice"})
    resp = client.post(f"/rooms/{code}/dev/end-game")
    assert resp.status_code == 404


def test_dev_autofill_authoring_deals_hands() -> None:
    room = Room("DEVFF1")
    room.add_player("p1", "Alice")
    room.add_player("p2", "Bob")

    asyncio.run(room.dev_autofill_authoring())

    assert room.state.phase == "playing"
    assert len(room.state.get_player("p1").hand) == STARTING_HAND_SIZE + room.state.draw_count
    assert len(room.state.get_player("p2").hand) == STARTING_HAND_SIZE
    assert all(room._authored_count(pid) >= CARDS_TO_AUTHOR for pid in ("p1", "p2"))
