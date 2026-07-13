"""Tests for the /ws/{room_code} WebSocket endpoint."""

from __future__ import annotations

import logging
import time

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from board.app import create_app


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


def test_spectator_can_join_ws_and_receive_state(client: TestClient) -> None:
    """A late joiner seated as a spectator still gets a valid player_id that
    opens the WebSocket and replays state — spectators live in their own
    GameState.spectators collection, not `players`, but must remain
    connectable exactly like a real player."""
    code = client.post("/rooms").json()["code"]
    client.post(f"/rooms/{code}/join", json={"name": "Alice"})
    # Start the game so the room leaves the lobby before the next joiner.
    from board.rooms.manager import room_manager

    room = room_manager.get(code)
    room.state = room.state.model_copy(update={"phase": "playing"})

    join_resp = client.post(f"/rooms/{code}/join", json={"name": "Late"}).json()
    assert join_resp["spectator"] is True
    pid = join_resp["player_id"]

    with client.websocket_connect(f"/ws/{code}") as ws:
        ws.send_json({"type": "join", "player_id": pid, "name": "Late"})
        msg = ws.receive_json()
        assert msg["type"] == "state"


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


def _seat(client: TestClient, name: str = "Alice") -> tuple[str, str]:
    code = client.post("/rooms").json()["code"]
    pid = client.post(f"/rooms/{code}/join", json={"name": name}).json()["player_id"]
    return code, pid


def _await_handler_teardown(caplog: pytest.LogCaptureFixture, pid: str, timeout: float = 2.0) -> bool:
    """Poll caplog for the ws handler's disconnect log — proof its teardown ran."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        for record in caplog.records:
            message = record.getMessage()
            if "disconnected" in message and pid in message:
                return True
        time.sleep(0.01)
    return False


def test_duplicate_join_replaces_old_socket_with_4009(client: TestClient) -> None:
    """A second socket for the same player_id evicts the first with close 4009,
    and the room keeps serving the replacement socket."""
    code, pid = _seat(client)
    with client.websocket_connect(f"/ws/{code}") as ws1:
        ws1.send_json({"type": "join", "player_id": pid, "name": "Alice"})
        assert ws1.receive_json()["type"] == "state"
        with client.websocket_connect(f"/ws/{code}") as ws2:
            ws2.send_json({"type": "join", "player_id": pid, "name": "Alice"})
            assert ws2.receive_json()["type"] == "state"
            with pytest.raises(WebSocketDisconnect) as exc:
                ws1.receive_text()
            assert exc.value.code == 4009
            ws2.send_text("not json")
            assert ws2.receive_json()["type"] == "error"


def test_replaced_handler_teardown_does_not_evict_new_socket(
    client: TestClient, caplog: pytest.LogCaptureFixture
) -> None:
    """When the replaced socket's client disconnects, the old handler's teardown
    must NOT unregister the replacement socket (identity-aware disconnect)."""
    from board.rooms.manager import room_manager

    code, pid = _seat(client)
    room = room_manager.get(code)
    with caplog.at_level(logging.INFO, logger="board.ws"):
        with client.websocket_connect(f"/ws/{code}") as ws1:
            ws1.send_json({"type": "join", "player_id": pid, "name": "Alice"})
            assert ws1.receive_json()["type"] == "state"
            with client.websocket_connect(f"/ws/{code}") as ws2:
                ws2.send_json({"type": "join", "player_id": pid, "name": "Alice"})
                assert ws2.receive_json()["type"] == "state"

                ws1.close()
                assert _await_handler_teardown(caplog, pid)
                assert room.connections.get(pid) is not None

                pid_bob = client.post(f"/rooms/{code}/join", json={"name": "Bob"}).json()["player_id"]
                with client.websocket_connect(f"/ws/{code}") as ws_bob:
                    ws_bob.send_json({"type": "join", "player_id": pid_bob, "name": "Bob"})
                    assert ws_bob.receive_json()["type"] == "state"
                    # the broadcast still reaches the replacement socket
                    assert ws2.receive_json()["type"] == "state"


def test_replaced_socket_wakeup_is_clean(client: TestClient, caplog: pytest.LogCaptureFixture) -> None:
    """A replaced handler that wakes up with a queued client message must exit
    through the normal disconnect path — no unhandled RuntimeError, and no
    eviction of the replacement socket."""
    from board.rooms.manager import room_manager

    code, pid = _seat(client)
    room = room_manager.get(code)
    with caplog.at_level(logging.INFO, logger="board.ws"):
        with client.websocket_connect(f"/ws/{code}") as ws1:
            ws1.send_json({"type": "join", "player_id": pid, "name": "Alice"})
            assert ws1.receive_json()["type"] == "state"
            with client.websocket_connect(f"/ws/{code}") as ws2:
                ws2.send_json({"type": "join", "player_id": pid, "name": "Alice"})
                assert ws2.receive_json()["type"] == "state"

                # Wake the old handler: it processes this message, then its next
                # receive on the already-closed socket raises RuntimeError.
                ws1.send_json({"type": "pass"})
                assert _await_handler_teardown(caplog, pid)
                assert room.connections.get(pid) is not None

                # The pass's error reply routed to the live replacement socket.
                assert ws2.receive_json()["type"] == "error"
                with pytest.raises(WebSocketDisconnect) as exc:
                    ws1.receive_text()
                assert exc.value.code == 4009
