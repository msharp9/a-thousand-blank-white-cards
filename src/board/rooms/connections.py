"""board.rooms.connections — per-room WebSocket registry.

A pure async utility: tracks live WebSocket connections keyed by player_id,
handles broadcast/targeted-send/disconnect. Knows nothing about game logic.
"""

from __future__ import annotations

import json
import logging

from fastapi import WebSocket

logger = logging.getLogger(__name__)


class ConnectionManager:
    """Manages WebSocket connections for one Room (single event loop)."""

    def __init__(self) -> None:
        self._connections: dict[str, WebSocket] = {}

    def connect(self, player_id: str, websocket: WebSocket) -> None:
        """Register or re-register a WebSocket for a player."""
        self._connections[player_id] = websocket
        logger.debug("player %s connected (%d total)", player_id, len(self._connections))

    def disconnect(self, player_id: str, websocket: WebSocket | None = None) -> None:
        """Remove a player's socket (they may rejoin later).

        When `websocket` is given, the mapping is removed only if it still points
        at that socket — a handler tearing down after being replaced by a newer
        connection must not evict its replacement.
        """
        if websocket is not None and self._connections.get(player_id) is not websocket:
            return
        self._connections.pop(player_id, None)
        logger.debug("player %s disconnected", player_id)

    def get(self, player_id: str) -> WebSocket | None:
        """Return the registered socket for a player, or None."""
        return self._connections.get(player_id)

    @property
    def connected_players(self) -> list[str]:
        return list(self._connections.keys())

    async def send(self, player_id: str, message: dict) -> None:
        """Send a JSON message to a single player (no-op if not connected)."""
        ws = self._connections.get(player_id)
        if ws is None:
            return
        try:
            await ws.send_text(json.dumps(message))
        except Exception as exc:
            logger.warning("send to %s failed: %s", player_id, exc)
            self.disconnect(player_id, ws)

    async def broadcast(self, message: dict) -> None:
        """Broadcast a JSON message to ALL connected players."""
        payload = json.dumps(message)
        dead: list[tuple[str, WebSocket]] = []
        for pid, ws in list(self._connections.items()):
            try:
                await ws.send_text(payload)
            except Exception as exc:
                logger.warning("broadcast to %s failed: %s", pid, exc)
                dead.append((pid, ws))
        for pid, ws in dead:
            self.disconnect(pid, ws)

    async def broadcast_state(self, state_snapshot: dict) -> None:
        """Wrap a snapshot in the 'state' envelope and broadcast."""
        await self.broadcast({"type": "state", "state": state_snapshot})
