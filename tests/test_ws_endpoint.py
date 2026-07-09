"""Tests for the /ws/{room_code} WebSocket endpoint."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from tbwc.app import create_app


@pytest.fixture
def client() -> TestClient:
    return TestClient(create_app())


def test_unknown_room_errors_and_closes(client: TestClient) -> None:
    with client.websocket_connect("/ws/NOPE99") as ws:
        msg = ws.receive_json()
        assert msg["type"] == "error"
        assert "not found" in msg["message"]


def test_join_with_valid_player_replays_state(client: TestClient) -> None:
    code = client.post("/rooms").json()["code"]
    pid = client.post(f"/rooms/{code}/join", json={"name": "Alice"}).json()["player_id"]
    with client.websocket_connect(f"/ws/{code}") as ws:
        ws.send_json({"type": "join", "player_id": pid, "name": "Alice"})
        msg = ws.receive_json()
        assert msg["type"] == "state"
        assert "state" in msg


def test_join_with_unknown_player_rejected(client: TestClient) -> None:
    code = client.post("/rooms").json()["code"]
    with client.websocket_connect(f"/ws/{code}") as ws:
        ws.send_json({"type": "join", "player_id": "ghost", "name": "X"})
        msg = ws.receive_json()
        assert msg["type"] == "error"


def test_first_message_must_be_join(client: TestClient) -> None:
    code = client.post("/rooms").json()["code"]
    client.post(f"/rooms/{code}/join", json={"name": "Alice"})
    with client.websocket_connect(f"/ws/{code}") as ws:
        ws.send_json({"type": "draw"})  # not a join
        msg = ws.receive_json()
        assert msg["type"] == "error"
        assert "join" in msg["message"].lower()
