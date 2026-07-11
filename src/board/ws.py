"""board.ws — WebSocket endpoint /ws/{room_code} for a game room.

Protocol: client connects, sends a 'join' envelope (with player_id from the REST
join stored in localStorage; echoed on reconnect), then exchanges typed JSON.
On disconnect the socket is unregistered but the seat is kept.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from pydantic import TypeAdapter, ValidationError

from models.ws_messages import ClientMsg, JoinMsg
from board.rooms.manager import room_manager

logger = logging.getLogger(__name__)

router = APIRouter()

_client_msg_adapter: TypeAdapter[ClientMsg] = TypeAdapter(ClientMsg)


@router.websocket("/ws/{room_code}")
async def ws_handler(websocket: WebSocket, room_code: str) -> None:
    """WebSocket handler for a game room (join -> message loop -> disconnect).

    Protocol (full reference lives in the OpenAPI description in board.app and the
    README "WebSocket API" section):

      1. Client connects to /ws/{room_code}; an unknown room is rejected (4004).
      2. First message MUST be a `join` envelope with a `player_id` previously
         issued by POST /rooms/{code}/join (else 4000/4001). A second socket for
         the same player replaces the older one (4009).
      3. Server replays a full `state` snapshot, then loops: it validates each
         client message (join/start/pass/play/create_card/preview_card/
         epilogue_vote) and dispatches to the room, broadcasting server messages
         (state, brewing, card_interpreted, effect_applied, preview_result,
         prompt_choice, epilogue, error). Invalid JSON yields an `error` reply.
         A `play` of a BLANK card additionally carries the authored
         `title`+`description`, which the room persists onto the card (author on
         play) before interpreting it.
    """
    code = room_code.upper()
    room = room_manager.get(code)
    if room is None:
        await websocket.accept()
        await websocket.send_json({"type": "error", "message": f"Room '{code}' not found"})
        await websocket.close(code=4004)
        return

    await websocket.accept()

    # First message MUST be a join.
    try:
        raw = await websocket.receive_text()
        join_msg = _client_msg_adapter.validate_json(raw)
    except ValidationError as exc:
        await websocket.send_json({"type": "error", "message": f"Expected 'join' message: {exc}"})
        await websocket.close(code=4000)
        return

    if not isinstance(join_msg, JoinMsg):
        await websocket.send_json({"type": "error", "message": "First message must be type=join"})
        await websocket.close(code=4000)
        return

    player_id = join_msg.player_id
    if player_id is None or player_id not in room.get_player_ids():
        await websocket.send_json(
            {"type": "error", "message": "player_id not found in this room — join via POST /rooms/{code}/join first"}
        )
        await websocket.close(code=4001)
        return

    # If this player already has an open socket (duplicate tab / stale connection),
    # close the old one before rebinding (4009 = replaced by new connection).
    old_ws = room.connections._connections.get(player_id)
    if old_ws is not None and old_ws is not websocket:
        try:
            await old_ws.close(code=4009)
        except Exception:
            pass

    room.connections.connect(player_id, websocket)
    logger.info("player %s connected to room %s", player_id, code)

    # Replay full state (covers reconnect).
    await room.connections.broadcast_state(room.snapshot())

    try:
        while True:
            raw = await websocket.receive_text()
            try:
                msg = _client_msg_adapter.validate_json(raw)
            except ValidationError as exc:
                await websocket.send_json({"type": "error", "message": str(exc)})
                continue
            await room.handle_action(player_id, msg)
    except WebSocketDisconnect:
        logger.info("player %s disconnected from room %s", player_id, code)
        room.connections.disconnect(player_id)
