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
    real_players = [p for p in data["players"] if not p["spectator"]]
    assert real_players
    for p in real_players:
        assert len(p["hand"]) == STARTING_HAND_SIZE
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


def test_dev_end_game_opens_epilogue(dev_client: TestClient) -> None:
    code = dev_client.post("/rooms").json()["code"]
    dev_client.post(f"/rooms/{code}/join", json={"name": "Alice"})
    dev_client.post(f"/rooms/{code}/join", json={"name": "Bob"})
    assert dev_client.post(f"/rooms/{code}/dev/skip-setup").status_code == 200

    resp = dev_client.post(f"/rooms/{code}/dev/end-game")
    assert resp.status_code == 200
    data = resp.json()
    assert data["phase"] in ("epilogue", "ended")
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
    assert len(room.state.get_player("p1").hand) == STARTING_HAND_SIZE
    assert len(room.state.get_player("p2").hand) == STARTING_HAND_SIZE
    assert all(room._authored_count(pid) >= CARDS_TO_AUTHOR for pid in ("p1", "p2"))
