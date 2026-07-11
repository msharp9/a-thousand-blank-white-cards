"""Tests for the /rooms REST routes."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app import create_app


@pytest.fixture
def client() -> TestClient:
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
    from rooms.manager import room_manager

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
