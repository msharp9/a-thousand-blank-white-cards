"""rooms.store — pluggable storage backend for active game rooms.

This module defines the ``RoomStore`` seam so the room registry is not hard-wired
to a single in-process dict. Today the only implementation is
``InMemoryRoomStore`` (process-local, single-worker only). The Protocol exists so
a future distributed backend (e.g. Redis, a shared coordinator) can be dropped in
WITHOUT touching :class:`rooms.manager.RoomManager` — see the multi-worker
note there.

Codes are expected to already be normalised to upper-case by the caller
(``RoomManager``); implementations key on the code as given.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from rooms.room import Room


@runtime_checkable
class RoomStore(Protocol):
    """Storage backend for active :class:`~rooms.room.Room` instances.

    A future distributed implementation (Redis, etc.) only needs to satisfy this
    Protocol; ``RoomManager`` delegates all persistence to it.
    """

    def get(self, code: str) -> Room | None:
        """Return the room for ``code``, or ``None`` if it is not stored."""
        ...

    def put(self, code: str, room: Room) -> None:
        """Store ``room`` under ``code`` (overwriting any existing entry)."""
        ...

    def exists(self, code: str) -> bool:
        """Return ``True`` if a room is stored under ``code``."""
        ...

    def count(self) -> int:
        """Return the number of rooms currently stored."""
        ...


class InMemoryRoomStore:
    """In-process ``RoomStore`` backed by a plain dict.

    PROCESS-LOCAL and SINGLE-WORKER ONLY: rooms live only in the memory of the
    worker that created them, and are cleared on restart. See
    :class:`rooms.manager.RoomManager` for the multi-worker hazard this
    implies and the guard that surfaces it at startup.
    """

    def __init__(self) -> None:
        self._rooms: dict[str, Room] = {}

    def get(self, code: str) -> Room | None:
        return self._rooms.get(code)

    def put(self, code: str, room: Room) -> None:
        self._rooms[code] = room

    def exists(self, code: str) -> bool:
        return code in self._rooms

    def count(self) -> int:
        return len(self._rooms)
