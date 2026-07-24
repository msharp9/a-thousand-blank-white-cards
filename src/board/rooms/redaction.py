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


def _visible_card_ids(snap: dict[str, Any], viewer_id: str | None) -> set[str]:
    """Card ids whose CONTENT ``viewer_id`` is entitled to see.

    Public zones (in_play, discard, center), the viewer's own hand, and the
    deck while it is the shared setup pool. Beyond zones, content that has
    already been revealed to the table stays visible: the card suspended in an
    open reaction window (``pending_play``), every card a history event names
    (played cards remain readable even if they later move to a hidden zone),
    and the epilogue vote outcomes.
    """
    visible: set[str] = set()
    for player in snap.get("players", []):
        visible.update(player.get("in_play", []))
        if player.get("id") == viewer_id:
            visible.update(player.get("hand", []))
    visible.update(snap.get("discard", []))
    visible.update(snap.get("house_rules", []))
    if snap.get("phase") in PUBLIC_DECK_PHASES:
        visible.update(snap.get("deck", []))
    pending_play = snap.get("pending_play")
    if pending_play is not None:
        visible.add(pending_play.get("card_id"))
    for event in snap.get("history_events", []):
        card_id = event.get("card_id")
        if card_id is not None:
            visible.add(card_id)
    epilogue = snap.get("epilogue_result")
    if epilogue is not None:
        for outcome in [*epilogue.get("kept", []), *epilogue.get("destroyed", [])]:
            visible.add(outcome.get("id"))
    return visible


def redact_snapshot(snap: dict[str, Any], viewer_id: str | None) -> dict[str, Any]:
    """Return a copy of ``snap`` redacted for ``viewer_id``.

    - Every player entry gains ``hand_count``; any player OTHER than the
      viewer has their ``hand`` emptied. A ``viewer_id`` of None — or a
      spectator id, which never matches a player — yields the fully-hidden
      view (all hands redacted).
    - ``deck_count`` is always added; ``deck`` is emptied outside
      :data:`PUBLIC_DECK_PHASES`.
    - The ``cards`` registry is filtered to :func:`_visible_card_ids` — the
      id lists above only say WHERE cards are; the registry is what carries
      their content, so hidden entries must be dropped, not just de-listed.
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
    cards = snap.get("cards")
    if isinstance(cards, dict):
        visible = _visible_card_ids(snap, viewer_id)
        redacted["cards"] = {cid: card for cid, card in cards.items() if cid in visible}
    return redacted
