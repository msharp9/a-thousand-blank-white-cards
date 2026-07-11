"""Integration test: player disconnect -> reconnect -> full state replay."""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from board.app import create_app


@pytest.fixture
def client() -> TestClient:
    return TestClient(create_app())


def _create_and_join(client: TestClient) -> tuple[str, str]:
    code = client.post("/rooms").json()["code"]
    pid = client.post(f"/rooms/{code}/join", json={"name": "Alice"}).json()["player_id"]
    return code, pid


def _join_payload(player_id: str) -> str:
    return json.dumps({"type": "join", "player_id": player_id, "name": "Alice"})


def test_reconnect_replays_state(client: TestClient) -> None:
    code, pid = _create_and_join(client)
    with client.websocket_connect(f"/ws/{code}") as ws1:
        ws1.send_text(_join_payload(pid))
        msg1 = json.loads(ws1.receive_text())
        assert msg1["type"] == "state"
        first_state = msg1["state"]
    # ws1 closed on context exit (disconnect)

    with client.websocket_connect(f"/ws/{code}") as ws2:
        ws2.send_text(_join_payload(pid))
        msg2 = json.loads(ws2.receive_text())
        assert msg2["type"] == "state"
        assert msg2["state"] == first_state  # no moves happened -> identical


def test_unknown_player_id_rejected(client: TestClient) -> None:
    code, _ = _create_and_join(client)
    with client.websocket_connect(f"/ws/{code}") as ws:
        ws.send_text(json.dumps({"type": "join", "player_id": "not-a-real-id", "name": "X"}))
        msg = json.loads(ws.receive_text())
        assert msg["type"] == "error"


def test_unknown_room_rejected(client: TestClient) -> None:
    with client.websocket_connect("/ws/ZZZZZZ") as ws:
        msg = json.loads(ws.receive_text())
        assert msg["type"] == "error"
        assert "ZZZZZZ" in msg["message"]
