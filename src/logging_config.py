"""logging_config — the one central place for logging setup.

Call :func:`configure_logging` once at application startup (wired into the
FastAPI lifespan in :mod:`board.app`) to install a clean, readable formatter
(timestamp, level, logger name, message) on the root logger. Configuring the
root logger means every module logger (``board.*``, ``engine.*``, ``agent.*``,
… and third-party loggers) inherits the same format and level without each
module doing its own setup.

The function is idempotent: it replaces its own handler on repeat calls rather
than stacking duplicate handlers, so it is safe to call from tests or a
reloading server. It deliberately does NOT disable or rip out uvicorn's own
handlers — it only owns the root handler and the root level.
"""

from __future__ import annotations

import logging

from config import get_settings

# Marker attribute so we can find (and replace) the handler WE installed on the
# root logger, leaving uvicorn's / anyone else's handlers untouched.
_CENTRAL_HANDLER_NAME = "central"

_LOG_FORMAT = "%(asctime)s %(levelname)-8s %(name)s: %(message)s"
_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"


def configure_logging(level: str | None = None) -> None:
    """Install the central logging configuration (idempotent).

    ``level`` overrides the configured level; when omitted it is read from
    ``settings.log_level`` (default ``"INFO"``). Applies a single readable
    handler to the root logger and sets the root level. Safe to call more than
    once — the previously-installed handler is swapped out rather than
    duplicated.
    """
    if level is None:
        level = get_settings().log_level
    resolved = logging.getLevelName(level.upper() if isinstance(level, str) else level)
    if not isinstance(resolved, int):
        resolved = logging.INFO

    formatter = logging.Formatter(fmt=_LOG_FORMAT, datefmt=_DATE_FORMAT)

    root = logging.getLogger()
    # Remove any handler we installed on a previous call so we stay idempotent.
    for handler in list(root.handlers):
        if getattr(handler, "name", None) == _CENTRAL_HANDLER_NAME:
            root.removeHandler(handler)

    handler = logging.StreamHandler()
    handler.name = _CENTRAL_HANDLER_NAME
    handler.setFormatter(formatter)
    root.addHandler(handler)
    root.setLevel(resolved)


def log_game_state(state, logger: logging.Logger | None = None) -> None:
    """Log a compact one-line summary of a GameState (opt-in helper).

    Provided for callers that want a per-turn / on-demand state dump. It is NOT
    wired into the game loop here — the debug endpoint GET /rooms/{code}/state
    covers on-demand inspection — so nothing calls this automatically.
    """
    log = logger or logging.getLogger("game_state")
    room_code = getattr(state, "room_code", "?")
    phase = getattr(state, "phase", "?")
    players = getattr(state, "players", [])
    turn_index = getattr(state, "turn_index", "?")
    log.info(
        "room=%s phase=%s turn_index=%s players=%d",
        room_code,
        phase,
        turn_index,
        len(players),
    )
