"""tbwc.rooms.manager — process-level in-memory registry of active game rooms.

NOTE: rooms are NOT persisted — a server restart clears all games. Intentional for v1.
"""

from __future__ import annotations

import logging
import random
import string
import uuid

from tbwc.rooms.room import Room

logger = logging.getLogger(__name__)

_CODE_CHARS = string.ascii_uppercase + string.digits
_CODE_LENGTH = 6


def _generate_code() -> str:
    return "".join(random.choices(_CODE_CHARS, k=_CODE_LENGTH))


class RoomManager:
    """In-memory registry of all active rooms (cleared on server restart)."""

    def __init__(self) -> None:
        self._rooms: dict[str, Room] = {}

    def create_room(self) -> str:
        """Create a new Room and return its 6-char join code."""
        code = self._unique_code()
        self._rooms[code] = Room(code)
        logger.info("room %s created (%d active rooms)", code, len(self._rooms))
        return code

    def get(self, code: str) -> Room | None:
        """Return the Room for this code, or None if it doesn't exist."""
        return self._rooms.get(code.upper())

    def join(self, code: str, name: str) -> tuple[str, str] | None:
        """Add a player to the room. Returns (room_code, player_id) or None if missing.

        player_id is an opaque UUID token the client stores and echoes back on reconnect.
        """
        room = self.get(code)
        if room is None:
            return None
        player_id = str(uuid.uuid4())
        room.add_player(player_id=player_id, name=name)
        logger.info("player %s ('%s') joined room %s", player_id, name, code)
        return code, player_id

    def _unique_code(self) -> str:
        for _ in range(20):
            code = _generate_code()
            if code not in self._rooms:
                return code
        raise RuntimeError("Could not generate a unique room code after 20 attempts")


# Process-level singleton imported by REST routes and the WS handler.
room_manager = RoomManager()
