"""board.rooms.redaction — per-viewer snapshot redaction.

Hidden information (opponents' hand contents, the draw pile's contents and
order) must never leave the server: face-down rendering on the client is a
convention, not a secret. :func:`redact_snapshot` turns the full
``Room.snapshot()`` dict into the view that is safe to send to one viewer.
"""

from __future__ import annotations

from typing import Any

# Phases in which the deck's CONTENTS are deliberately public: during setup it
# holds the shared pre-made pool the authoring screen renders so players can
# write cards that synergize with it (see lobby→setup in Room.handle_start).
# From "playing" onward it is the shuffled draw pile, whose contents and order
# back scry / stacked-deck effects and must stay server-side.
PUBLIC_DECK_PHASES = frozenset({"lobby", "setup"})


def redact_snapshot(snap: dict[str, Any], viewer_id: str | None) -> dict[str, Any]:
    """Return a copy of ``snap`` redacted for ``viewer_id``.

    - Every player entry gains ``hand_count``; any player OTHER than the
      viewer has their ``hand`` emptied. A ``viewer_id`` of None — or a
      spectator id, which never matches a player — yields the fully-hidden
      view (all hands redacted).
    - ``deck_count`` is always added; ``deck`` is emptied outside
      :data:`PUBLIC_DECK_PHASES`.
    - ``discard``, ``in_play`` and the center zone are public and untouched.

    The input dict is never mutated; only the copied containers this function
    rewrites are duplicated.
    """
    redacted = dict(snap)
    players = []
    for player in snap.get("players", []):
        entry = dict(player)
        entry["hand_count"] = len(entry.get("hand", []))
        if entry.get("id") != viewer_id:
            entry["hand"] = []
        players.append(entry)
    redacted["players"] = players
    redacted["deck_count"] = len(snap.get("deck", []))
    if snap.get("phase") not in PUBLIC_DECK_PHASES:
        redacted["deck"] = []
    return redacted
