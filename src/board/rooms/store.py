"""board.rooms.store — pluggable storage backend for active game rooms.

This module defines the ``RoomStore`` seam so the room registry is not hard-wired
to a single in-process dict. Today the only implementation is
``InMemoryRoomStore`` (process-local, single-worker only). The Protocol exists so
a future distributed backend (e.g. Redis, a shared coordinator) can be dropped in
WITHOUT touching :class:`board.rooms.manager.RoomManager` — see the multi-worker
note there.

Codes are expected to already be normalised to upper-case by the caller
(``RoomManager``); implementations key on the code as given.

``FileRoomStore`` is a DEV-ONLY convenience (wired in only when
``get_settings().dev_mode`` is true) that persists each room to JSON on disk so
in-progress games survive an API reload. It is deliberately lossy: the Room
out-of-band ``Room.card_art`` registry (card art deliberately lives outside
GameState) is NOT persisted either — dev-mode art does not survive a process
restart, so restore resets ``has_art`` to False on any card whose art is no
longer in the registry (otherwise restored cards would advertise art that
404s). Regular serialized state — per-player ``conditions``, ``rules``,
``turn_order``, registered ``hooks`` (their per-room registry is rebuilt lazily
from state) — survives the round-trip. Acceptable for a dev loop; not a durable
multi-worker backend.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Protocol, runtime_checkable

from models.game_state import GameState
from board.rooms.epilogue import EpilogueManager
from board.rooms.interactions import PendingResolution
from board.rooms.room import Room

logger = logging.getLogger(__name__)

# Dev-only rooms are never reaped while live, so the on-disk registry grows
# without bound across playtests. Drop anything older than this on load to keep
# a dev loop from resurrecting thousands of stale rooms after a restart.
_ROOM_MAX_AGE = timedelta(days=1)


@runtime_checkable
class RoomStore(Protocol):
    """Storage backend for active :class:`~board.rooms.room.Room` instances.

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

    def values(self) -> list[Room]:
        """Return live rooms so startup can restore background deadlines."""
        ...


class InMemoryRoomStore:
    """In-process ``RoomStore`` backed by a plain dict.

    PROCESS-LOCAL and SINGLE-WORKER ONLY: rooms live only in the memory of the
    worker that created them, and are cleared on restart. See
    :class:`board.rooms.manager.RoomManager` for the multi-worker hazard this
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

    def values(self) -> list[Room]:
        return list(self._rooms.values())


class FileRoomStore:
    """DEV-ONLY ``RoomStore`` that persists rooms to JSON on disk.

    Live :class:`Room` objects (with their asyncio.Lock and ConnectionManager)
    are cached in ``self._rooms`` and reused within one process — disk is only
    the cold-start source of truth, rehydrated once via :meth:`load_all` at
    construction. Writes are best-effort: a failure logs a warning and never
    propagates, so persistence can never crash a live game action.
    """

    def __init__(self, directory: str | Path = ".devstate/rooms") -> None:
        self._dir = Path(directory)
        self._rooms: dict[str, Room] = {}
        self._dir.mkdir(parents=True, exist_ok=True)
        self.load_all()

    def get(self, code: str) -> Room | None:
        return self._rooms.get(code)

    def put(self, code: str, room: Room) -> None:
        self._rooms[code] = room
        try:
            path = self._dir / f"{code}.json"
            path.write_text(json.dumps(_room_to_dict(room)))
        except Exception:
            logger.warning("failed to persist room %s to disk", code, exc_info=True)

    def exists(self, code: str) -> bool:
        return code in self._rooms

    def count(self) -> int:
        return len(self._rooms)

    def values(self) -> list[Room]:
        return list(self._rooms.values())

    def load_all(self) -> None:
        """Rehydrate every persisted room from disk, skipping unreadable files.

        Rooms older than ``_ROOM_MAX_AGE`` are deleted rather than loaded so the
        dev registry stays bounded across restarts; deletion is best-effort.
        """
        cutoff = datetime.now(UTC) - _ROOM_MAX_AGE
        for path in self._dir.glob("*.json"):
            try:
                data = json.loads(path.read_text())
            except Exception:
                logger.warning("skipping unreadable room file %s", path, exc_info=True)
                continue
            # A stale room is dropped rather than loaded so the dev registry
            # stays bounded on restart. A timestamp-less file (pre-created_at
            # format) is left to load and backfill — see _room_from_dict.
            created_at = _parse_created_at(data.get("created_at"))
            if created_at is not None and created_at < cutoff:
                path.unlink(missing_ok=True)
                continue
            try:
                room = _room_from_dict(data)
            except Exception:
                logger.warning("skipping unreadable room file %s", path, exc_info=True)
                continue
            self._rooms[room.code] = room

    def rewire_on_change(self, cb: Callable[[Room], None]) -> None:
        """Attach ``cb`` as the on_change hook on every already-loaded room.

        Rooms rehydrated by :meth:`load_all` predate the RoomManager, so their
        persistence hook must be wired after the manager exists.
        """
        for room in self._rooms.values():
            room.on_change = cb


def _parse_created_at(raw: object) -> datetime | None:
    """Parse a persisted ``created_at`` to aware UTC, or ``None`` if unusable."""
    if not isinstance(raw, str):
        return None
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)


def _room_to_dict(room: Room) -> dict:
    data = {
        "code": room.code,
        "simple": room._simple,
        "created_at": room.created_at.isoformat(),
        "state": room.state.model_dump(mode="json"),
        "turn_state": {
            "has_drawn": room._has_drawn,
            "plays_this_turn": room._plays_this_turn,
            "deck_exhausted": room._deck_exhausted,
        },
    }
    if room._epilogue is not None:
        data["epilogue"] = room._epilogue.to_dict()
    if room._pending_resolution is not None:
        data["pending_resolution"] = room._pending_resolution.model_dump(mode="json")
    return data


def _room_from_dict(data: dict) -> Room:
    room = Room(data["code"], mode=data["state"]["mode"], simple=data["simple"])
    created_at = data.get("created_at")
    if created_at is not None:
        room.created_at = datetime.fromisoformat(created_at)
    state = GameState.model_validate(data["state"])
    # A room persisted before turn_number existed (or any mid-game state whose
    # count was never bumped) restores as 0, violating the >=1 contract once the
    # game has left the lobby. Backfill so the "Turn N" display and any
    # turn-number logic stay honest across a restart.
    if state.phase in {"playing", "results", "epilogue", "ended"} and state.turn_number < 1:
        state = state.model_copy(update={"turn_number": 1})
    # card_art is not persisted (module docstring): clear has_art on any card
    # whose art did not survive the restart so clients never fetch a 404.
    cards = {
        cid: (
            {**card, "has_art": False}
            if isinstance(card, dict) and card.get("has_art") and cid not in room.card_art
            else card
        )
        for cid, card in state.cards.items()
    }
    room.state = state.model_copy(update={"cards": cards})
    turn_state = data.get("turn_state") or {}
    room._has_drawn = bool(turn_state.get("has_drawn", False))
    room._plays_this_turn = int(turn_state.get("plays_this_turn", 0))
    room._deck_exhausted = bool(turn_state.get("deck_exhausted", False))
    epilogue_data = data.get("epilogue")
    if epilogue_data is not None:
        room._epilogue = EpilogueManager.from_dict(epilogue_data, room.connections)
    pending_data = data.get("pending_resolution")
    if pending_data is not None:
        room._pending_resolution = PendingResolution.model_validate(pending_data)
    return room
