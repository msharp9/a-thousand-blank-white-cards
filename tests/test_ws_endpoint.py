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


def test_unknown_room_closes_with_4004(client: TestClient) -> None:
    """A WS connect to a room this worker doesn't have fails cleanly (no crash).

    This is the exact failure surface of the multi-worker hazard: if REST join
    landed on a different worker, the WS worker won't find the room. The handler
    must send a clean error envelope and close with 4004 rather than raise.
    """
    from starlette.websockets import WebSocketDisconnect

    with client.websocket_connect("/ws/GHOST1") as ws:
        assert ws.receive_json()["type"] == "error"
        with pytest.raises(WebSocketDisconnect) as exc:
            ws.receive_text()
        assert exc.value.code == 4004


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
        ws.send_json({"type": "pass"})  # not a join
        msg = ws.receive_json()
        assert msg["type"] == "error"
        assert "join" in msg["message"].lower()


def test_two_players_stay_connected_with_distinct_ids(client: TestClient) -> None:
    """Two players with distinct player_ids can both connect and stay connected.

    Regression test for the bug where a shared client-side player_id caused the
    second connection to evict the first (server closes the older socket, 4009).
    With distinct ids (as the per-room/per-tab client scoping now produces), both
    sockets stay open and both receive their state replay.
    """
    code = client.post("/rooms").json()["code"]
    pid1 = client.post(f"/rooms/{code}/join", json={"name": "Alice"}).json()["player_id"]
    pid2 = client.post(f"/rooms/{code}/join", json={"name": "Bob"}).json()["player_id"]
    assert pid1 != pid2

    with client.websocket_connect(f"/ws/{code}") as ws1:
        ws1.send_json({"type": "join", "player_id": pid1, "name": "Alice"})
        assert ws1.receive_json()["type"] == "state"

        with client.websocket_connect(f"/ws/{code}") as ws2:
            ws2.send_json({"type": "join", "player_id": pid2, "name": "Bob"})
            # Bob connecting broadcasts state to all sockets; both must receive it,
            # proving ws1 was NOT evicted by ws2 connecting.
            assert ws2.receive_json()["type"] == "state"
            assert ws1.receive_json()["type"] == "state"
