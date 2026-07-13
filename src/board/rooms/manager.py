"""board.rooms.manager — registry of active game rooms.

NOTE: in production rooms are NOT persisted — a server restart clears all games.
Under DEV_MODE the singleton uses FileRoomStore so games survive a --reload; see
_build_room_manager and board.rooms.store.
"""

from __future__ import annotations

import logging
import random
import string
import uuid

from config import get_settings

from board.rooms.room import Room
from board.rooms.store import FileRoomStore, InMemoryRoomStore, RoomStore

logger = logging.getLogger(__name__)

_CODE_CHARS = string.ascii_uppercase + string.digits
_CODE_LENGTH = 6


def _generate_code() -> str:
    return "".join(random.choices(_CODE_CHARS, k=_CODE_LENGTH))


class RoomManager:
    """Registry of all active rooms, delegating storage to a :class:`RoomStore`.

    SINGLE-WORKER GUARANTEE / DISTRIBUTED SEAM
    ------------------------------------------
    Room lookup is only correct when the whole app runs as a SINGLE worker
    process. The default backend (:class:`~board.rooms.store.InMemoryRoomStore`)
    keeps rooms in that one worker's memory, so REST join
    (POST /rooms/{code}/join) and the WS connect (/ws/{code}) always hit the same
    process and see the same rooms. This is the app's current deployment model
    (see render.yaml / Dockerfile: a single ``uvicorn`` process, no ``--workers``).

    With more than one worker (``uvicorn --workers N``, gunicorn, or multiple
    containers) those two requests can land on DIFFERENT workers, and the WS
    worker would not have the room in its store — rejecting a valid player as
    "room not found" / "player_id not found". :func:`check_single_worker` is
    called at startup to warn loudly if a multi-worker configuration is detected.

    ``RoomStore`` is the seam for fixing that properly: a future distributed
    backend (Redis, a shared coordinator, etc.) can implement the Protocol and be
    injected here without changing this class. That larger change is deliberately
    out of scope for the in-process fix and still warrants its own bead.
    """

    def __init__(self, store: RoomStore | None = None) -> None:
        self._store: RoomStore = store if store is not None else InMemoryRoomStore()

    def create_room(self, mode: str = "both") -> str:
        """Create a new Room and return its 6-char join code."""
        code = self._unique_code()
        room = Room(code, mode=mode, on_change=self._persist)
        self._store.put(code, room)
        logger.info("room %s created (%d active rooms)", code, self._store.count())
        return code

    def _persist(self, room: Room) -> None:
        self._store.put(room.code, room)

    def get(self, code: str) -> Room | None:
        """Return the Room for this code, or None if it doesn't exist."""
        return self._store.get(code.upper())

    def list_rooms(self) -> list[Room]:
        """Return every stored room, in no particular order.

        Filtering (joinable vs all-non-ended) and sorting are presentation
        concerns owned by the caller (see GET /rooms in board.app).
        """
        return self._store.values()

    def start_background_tasks(self) -> None:
        """Restore persisted room timers once an application event loop exists."""
        for room in self._store.values():
            room.ensure_pending_timeout()

    def join(self, code: str, name: str) -> tuple[str, str, bool] | None:
        """Add a player to the room. Returns (room_code, player_id, spectator) or None.

        Join policy lives here: a joiner arriving while the room is still in the
        ``lobby`` phase becomes a normal player; a joiner arriving after the game
        has started (any non-lobby phase — setup/playing/epilogue/ended) becomes a
        SPECTATOR. Spectators still get a valid ``player_id`` so they can open the
        WebSocket and receive state broadcasts, but they take no turn and cannot
        author or play cards.

        player_id is an opaque UUID token the client stores and echoes back on reconnect.
        """
        room = self.get(code)
        if room is None:
            return None
        player_id = str(uuid.uuid4())
        spectator = room.state.phase != "lobby"
        if spectator:
            room.add_spectator(player_id=player_id, name=name)
        else:
            room.add_player(player_id=player_id, name=name)
        self._persist(room)
        logger.info(
            "%s %s ('%s') joined room %s",
            "spectator" if spectator else "player",
            player_id,
            name,
            code,
        )
        return code, player_id, spectator

    def _unique_code(self) -> str:
        for _ in range(20):
            code = _generate_code()
            if not self._store.exists(code):
                return code
        raise RuntimeError("Could not generate a unique room code after 20 attempts")


def _detect_worker_count() -> int | None:
    """Best-effort read of the configured worker count from the environment.

    Honours ``WEB_CONCURRENCY`` (the de-facto standard uvicorn/gunicorn env var).
    Returns ``None`` when the value is unset or unparseable, in which case the
    caller assumes the app's documented single-worker deployment.
    """
    import os

    raw = os.environ.get("WEB_CONCURRENCY")
    if raw is None:
        return None
    try:
        return int(raw)
    except ValueError:
        logger.warning("WEB_CONCURRENCY=%r is not an integer; ignoring", raw)
        return None


def check_single_worker(worker_count: int | None = None) -> None:
    """Warn if the app appears to be configured for more than one worker.

    The default :class:`InMemoryRoomStore` is process-local, so a multi-worker
    deployment silently breaks REST-join → WS-connect. This surfaces that at
    startup instead of as a confusing "room not found" for real players. Pass
    ``worker_count`` explicitly in tests; otherwise it is detected from the
    environment.
    """
    count = worker_count if worker_count is not None else _detect_worker_count()
    if count is not None and count > 1:
        logger.warning(
            "Detected %d workers (WEB_CONCURRENCY) but RoomManager uses the "
            "process-local InMemoryRoomStore. Rooms are NOT shared across "
            "workers, so REST join and WS connect can land on different workers "
            "and reject valid players. Run a SINGLE worker, or implement a "
            "shared RoomStore (see board.rooms.store) before scaling out.",
            count,
        )


def _build_room_manager() -> RoomManager:
    """Pick the room store based on dev_mode.

    In dev_mode a FileRoomStore persists rooms to disk and rehydrates them on
    startup; the manager then rewires its persistence hook onto any room loaded
    before it existed. Production keeps the process-local InMemoryRoomStore, so
    no files are ever written.
    """
    if get_settings().dev_mode:
        store = FileRoomStore()
        manager = RoomManager(store=store)
        store.rewire_on_change(manager._persist)
        return manager
    return RoomManager()


# Process-level singleton imported by REST routes and the WS handler.
# NOTE: single-worker only — see RoomManager docstring and check_single_worker().
room_manager = _build_room_manager()
